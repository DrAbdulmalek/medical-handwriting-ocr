"""
OCR Engine for Medical Handwriting Recognition.

Wraps PaddleOCR (Arabic + English) and provides:
- Text region detection with bounding boxes
- Arabic text correction via ``arabic_utils``
- Region cropping as base64-encoded PNGs for downstream review
"""

import base64
import logging
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from paddleocr import PaddleOCR

from app.arabic_utils import fix_arabic_text, is_arabic

logger = logging.getLogger(__name__)


class OCREngine:
    """PaddleOCR wrapper with Arabic RTL text correction."""

    def __init__(self):
        self.paddle = PaddleOCR(
            use_angle_cls=True,
            lang='ar',
            show_log=False,
            use_gpu=False,  # HF Spaces typically CPU-only
        )
        logger.info("PaddleOCR initialized (CPU mode)")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_regions(self, image_path: str) -> List[Dict]:
        """Detect text regions and return dicts with bbox, text, confidence."""
        result = self.paddle.ocr(image_path, cls=True)

        regions: List[Dict] = []
        if result and result[0]:
            for idx, line in enumerate(result[0]):
                if not line:
                    continue
                bbox = line[0]
                text = line[1][0] if len(line) > 1 else ""
                confidence = line[1][1] if len(line) > 1 else 0.0

                x_coords = [p[0] for p in bbox]
                y_coords = [p[1] for p in bbox]

                # Fix Arabic text for correct RTL display
                display_text = fix_arabic_text(text)

                regions.append({
                    "bbox": {
                        "x1": int(min(x_coords)),
                        "y1": int(min(y_coords)),
                        "x2": int(max(x_coords)),
                        "y2": int(max(y_coords)),
                    },
                    "predicted_text": display_text,
                    "confidence": float(confidence),
                    "reading_order": idx,
                })

        return regions

    def detect_regions_with_crops(
        self,
        image_path: str,
        padding: int = 10,
    ) -> List[Dict]:
        """Detect regions AND return cropped images as base64-encoded PNGs.

        Each returned dict contains all fields from ``detect_regions`` plus:
        - ``crop_base64``: base64-encoded PNG of the cropped region (with padding)

        Parameters
        ----------
        image_path: str
            Path to the input image.
        padding: int
            Pixels of padding around each crop.

        Returns
        -------
        list[dict]
        """
        regions = self.detect_regions(image_path)
        if not regions:
            return regions

        img = cv2.imread(image_path)
        if img is None:
            logger.error("Failed to read image: %s", image_path)
            return regions

        h, w = img.shape[:2]

        for region in regions:
            bbox = region["bbox"]
            x1 = max(0, bbox["x1"] - padding)
            y1 = max(0, bbox["y1"] - padding)
            x2 = min(w, bbox["x2"] + padding)
            y2 = min(h, bbox["y2"] + padding)

            crop = img[y1:y2, x1:x2]
            _, buffer = cv2.imencode('.png', crop)
            region["crop_base64"] = base64.b64encode(buffer).decode('utf-8')

        return regions

    def crop_region(
        self,
        image: np.ndarray,
        bbox: Dict,
        padding: int = 10,
    ) -> bytes:
        """Extract a single crop as PNG bytes."""
        h, w = image.shape[:2]
        x1 = max(0, bbox["x1"] - padding)
        y1 = max(0, bbox["y1"] - padding)
        x2 = min(w, bbox["x2"] + padding)
        y2 = min(h, bbox["y2"] + padding)

        crop = image[y1:y2, x1:x2]
        _, buffer = cv2.imencode('.png', crop)
        return buffer.tobytes()

    @staticmethod
    def classify_script(text: str) -> str:
        """Classify the script type of *text*."""
        has_arabic = any('\u0600' <= c <= '\u06FF' for c in text)
        has_latin = any(c.isascii() and c.isalpha() for c in text)

        if has_arabic and has_latin:
            return "mixed"
        elif has_arabic:
            return "arabic"
        elif has_latin:
            return "latin"
        return "numeric"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
ocr_engine = OCREngine()
