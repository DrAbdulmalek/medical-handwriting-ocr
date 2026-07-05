import cv2
import numpy as np
from PIL import Image
from paddleocr import PaddleOCR
from transformers import TrOCRProcessor, VisionEncoderDecoderModel
import torch
import logging
from typing import List, Dict, Tuple
import io

logger = logging.getLogger(__name__)


class OCREngine:
    def __init__(self):
        # Initialize PaddleOCR for detection + initial recognition
        # Supports Arabic and English
        self.paddle = PaddleOCR(
            use_angle_cls=True,
            lang='ar',  # Arabic model includes English support
            show_log=False,
            use_gpu=torch.cuda.is_available()
        )

        # TrOCR for refinement (lazy loading)
        self.trocr_processor = None
        self.trocr_model = None
        self.trocr_loaded = False

        logger.info("PaddleOCR initialized successfully")

    def detect_regions(self, image_path: str) -> List[Dict]:
        """
        Detect text regions using PaddleOCR
        Returns list of dicts with bbox, text, confidence
        """
        result = self.paddle.ocr(image_path, cls=True)

        regions = []
        if result and result[0]:
            for idx, line in enumerate(result[0]):
                if line:
                    bbox = line[0]  # [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
                    text = line[1][0] if len(line) > 1 else ""
                    confidence = line[1][1] if len(line) > 1 else 0.0

                    # Convert to flat bbox format
                    x_coords = [p[0] for p in bbox]
                    y_coords = [p[1] for p in bbox]

                    region = {
                        "bbox": {
                            "x1": int(min(x_coords)),
                            "y1": int(min(y_coords)),
                            "x2": int(max(x_coords)),
                            "y2": int(max(y_coords))
                        },
                        "predicted_text": text,
                        "confidence": float(confidence),
                        "reading_order": idx
                    }
                    regions.append(region)

        return regions

    def crop_region(self, image: np.ndarray, bbox: Dict, padding: int = 10) -> bytes:
        """
        Extract crop with padding, return as PNG bytes
        """
        h, w = image.shape[:2]

        x1 = max(0, bbox["x1"] - padding)
        y1 = max(0, bbox["y1"] - padding)
        x2 = min(w, bbox["x2"] + padding)
        y2 = min(h, bbox["y2"] + padding)

        crop = image[y1:y2, x1:x2]

        # Convert to bytes
        _, buffer = cv2.imencode('.png', crop)
        return buffer.tobytes()

    def classify_script(self, text: str) -> str:
        """
        Classify script type
        """
        has_arabic = any('\u0600' <= c <= '\u06FF' for c in text)
        has_latin = any(c.isascii() and c.isalpha() for c in text)

        if has_arabic and has_latin:
            return "mixed"
        elif has_arabic:
            return "arabic"
        elif has_latin:
            return "latin"
        else:
            return "numeric"

    def refine_with_trocr(self, crop_bytes: bytes) -> Tuple[str, float]:
        """
        Use TrOCR for better recognition on difficult crops
        (Lazy loaded - only used for low-confidence regions)
        """
        if not self.trocr_loaded:
            self._load_trocr()

        try:
            image = Image.open(io.BytesIO(crop_bytes)).convert("RGB")
            pixel_values = self.trocr_processor(image, return_tensors="pt").pixel_values

            with torch.no_grad():
                generated_ids = self.trocr_model.generate(pixel_values)

            generated_text = self.trocr_processor.batch_decode(
                generated_ids,
                skip_special_tokens=True
            )[0]

            # Confidence estimation (simplified)
            confidence = 0.85  # Would need proper scoring

            return generated_text, confidence

        except Exception as e:
            logger.error(f"TrOCR refinement failed: {e}")
            return None, 0.0

    def _load_trocr(self):
        """Lazy load TrOCR model"""
        logger.info("Loading TrOCR model...")
        self.trocr_processor = TrOCRProcessor.from_pretrained(
            "microsoft/trocr-base-handwritten"
        )
        self.trocr_model = VisionEncoderDecoderModel.from_pretrained(
            "microsoft/trocr-base-handwritten"
        )
        self.trocr_model.eval()
        self.trocr_loaded = True
        logger.info("TrOCR loaded successfully")


# Singleton instance
ocr_engine = OCREngine()
