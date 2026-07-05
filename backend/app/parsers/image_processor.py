"""
Medical Image Processor Module for Medical Handwriting OCR.

Provides advanced image understanding capabilities using Microsoft's
Florence-2 model for medical image captioning, object detection,
OCR with layout understanding, and region classification.

The model is lazily loaded on first use to avoid slow startup.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models
# =============================================================================


class DetectedObject(BaseModel):
    """A single object detected in a medical image."""

    object_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this detection",
    )
    label: str = Field(..., description="Object label (e.g. 'prescription_header', 'signature')")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Detection confidence score",
    )
    bbox: Dict[str, int] = Field(
        ...,
        description="Bounding box {x1, y1, x2, y2} in image pixel coordinates",
    )
    area: Optional[int] = Field(None, description="Bounding box area in pixels²")


class RegionClassification(BaseModel):
    """Classification of a specific region within a medical image."""

    region_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
    )
    region_type: str = Field(
        ...,
        description="Region type: 'header', 'body', 'signature', 'stamp', "
                    "'footer', 'table', 'logo', 'handwriting', 'printed_text'",
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    bbox: Dict[str, int] = Field(...)
    text_content: Optional[str] = Field(None, description="OCR text within the region")
    is_handwritten: Optional[bool] = Field(None, description="Whether region contains handwriting")


class MedicalImageResult(BaseModel):
    """Aggregated result of processing a medical image."""

    image_path: str = Field("", description="Path to the processed image")
    caption: str = Field("", description="Generated image caption")
    detected_objects: List[DetectedObject] = Field(default_factory=list)
    region_classifications: List[RegionClassification] = Field(default_factory=list)
    ocr_text: str = Field("", description="Full OCR text extracted from the image")
    layout_description: str = Field("", description="Structured layout description")
    has_arabic: bool = Field(False, description="Whether Arabic text was detected")
    image_dimensions: Optional[Dict[str, int]] = Field(
        None, description="Image width and height"
    )
    processing_time_ms: float = Field(0.0)
    warnings: List[str] = Field(default_factory=list)


# =============================================================================
# Region type definitions for Florence-2 prompting
# =============================================================================

_REGION_TYPES = [
    "header",
    "body text",
    "signature",
    "stamp",
    "footer",
    "table",
    "logo",
    "handwriting",
    "printed text",
    "prescription block",
]

_MEDICAL_OBJECT_LABELS = [
    "doctor name",
    "patient name",
    "date",
    "drug name",
    "dosage",
    "medical stamp",
    "signature",
    "prescription header",
    "hospital logo",
    "barcode",
    "QR code",
    "table",
]


# =============================================================================
# MedicalImageProcessor
# =============================================================================


class MedicalImageProcessor:
    """
    Advanced medical image processor using Florence-2.

    Provides:
    * **Image Captioning** – generate descriptive captions for medical images
    * **Object Detection** – detect specific medical document elements
    * **OCR with Layout** – extract text with structural understanding
    * **Region Classification** – classify document regions by type

    Uses lazy model loading pattern consistent with the existing
    ``OCREngine`` in ``app.ocr_engine``.
    """

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._device: Optional[str] = None
        self._model_loaded: bool = False
        self._florence_available: Optional[bool] = None
        self._transformers_available: Optional[bool] = None
        self._model_name: str = "microsoft/Florence-2-large"
        self._output_dir: str = str(settings.CROP_DIR)
        os.makedirs(self._output_dir, exist_ok=True)
        logger.info("MedicalImageProcessor initialized (model: %s)", self._model_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_medical_image(self, image_path: str) -> MedicalImageResult:
        """
        Full pipeline: caption, detect objects, OCR, and classify regions.

        Parameters
        ----------
        image_path : str
            Path to the medical image.

        Returns
        -------
        MedicalImageResult
            Comprehensive analysis result.
        """
        import time

        start = time.perf_counter()
        result = MedicalImageResult(image_path=image_path)

        try:
            # Get image dimensions
            dims = self._get_image_dimensions(image_path)
            if dims:
                result.image_dimensions = {"width": dims[0], "height": dims[1]}

            # Step 1: Caption
            try:
                result.caption = self.caption_image(image_path)
            except Exception as exc:
                result.warnings.append(f"Captioning failed: {exc}")
                logger.warning("Captioning failed: %s", exc)

            # Step 2: Object detection
            try:
                result.detected_objects = self.detect_objects(image_path)
            except Exception as exc:
                result.warnings.append(f"Object detection failed: {exc}")
                logger.warning("Object detection failed: %s", exc)

            # Step 3: OCR with layout
            try:
                ocr_text, layout_desc = self._ocr_with_layout(image_path)
                result.ocr_text = ocr_text
                result.layout_description = layout_desc
                result.has_arabic = self._contains_arabic(ocr_text)
            except Exception as exc:
                result.warnings.append(f"OCR failed: {exc}")
                logger.warning("OCR failed: %s", exc)

            # Step 4: Region classification
            try:
                result.region_classifications = self.classify_regions(image_path)
            except Exception as exc:
                result.warnings.append(f"Region classification failed: {exc}")
                logger.warning("Region classification failed: %s", exc)

        except Exception as exc:
            logger.error("Medical image processing failed for %s: %s", image_path, exc, exc_info=True)
            result.warnings.append(f"Processing error: {exc}")

        result.processing_time_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Medical image processed: %d objects, %d regions, %.1fms",
            len(result.detected_objects),
            len(result.region_classifications),
            result.processing_time_ms,
        )
        return result

    def detect_objects(self, image_path: str) -> List[DetectedObject]:
        """
        Detect medical-specific objects in an image.

        Parameters
        ----------
        image_path : str
            Path to the medical image.

        Returns
        -------
        list[DetectedObject]
            Detected objects sorted by confidence (highest first).
        """
        if not self._ensure_model():
            return []

        logger.info("Detecting objects in: %s", image_path)

        try:
            from PIL import Image as PILImage

            image = PILImage.open(image_path).convert("RGB")

            # Florence-2 object detection prompt
            prompt = "<OD>"
            inputs = self._processor(
                text=prompt, images=image, return_tensors="pt"
            ).to(self._device)

            import torch
            with torch.no_grad():
                generated_ids = self._model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=1024,
                    num_beams=3,
                    do_sample=False,
                )

            generated_text = self._processor.batch_decode(
                generated_ids, skip_special_tokens=False
            )[0]

            # Parse Florence-2 OD output
            objects = self._parse_od_output(generated_text, image.size)

            logger.info("Detected %d objects", len(objects))
            return sorted(objects, key=lambda o: o.confidence, reverse=True)

        except Exception as exc:
            logger.error("Object detection failed: %s", exc, exc_info=True)
            return []

    def caption_image(self, image_path: str) -> str:
        """
        Generate a descriptive caption for a medical image.

        Parameters
        ----------
        image_path : str
            Path to the medical image.

        Returns
        -------
        str
            Generated caption describing the image content.
        """
        if not self._ensure_model():
            return ""

        logger.info("Captioning image: %s", image_path)

        try:
            from PIL import Image as PILImage

            image = PILImage.open(image_path).convert("RGB")

            # Florence-2 captioning prompt
            prompt = "<MORE_DETAILED_CAPTION>"
            inputs = self._processor(
                text=prompt, images=image, return_tensors="pt"
            ).to(self._device)

            import torch
            with torch.no_grad():
                generated_ids = self._model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=256,
                    num_beams=3,
                    do_sample=False,
                )

            caption = self._processor.batch_decode(
                generated_ids, skip_special_tokens=True
            )[0]

            logger.info("Caption generated: %s", caption[:100])
            return caption

        except Exception as exc:
            logger.error("Image captioning failed: %s", exc, exc_info=True)
            return ""

    def classify_regions(self, image_path: str) -> List[RegionClassification]:
        """
        Classify regions of a medical image by type.

        Parameters
        ----------
        image_path : str
            Path to the medical image.

        Returns
        -------
        list[RegionClassification]
            Classified regions sorted by confidence.
        """
        if not self._ensure_model():
            return []

        logger.info("Classifying regions in: %s", image_path)

        regions: List[RegionClassification] = []

        try:
            from PIL import Image as PILImage

            image = PILImage.open(image_path).convert("RGB")

            # Use Florence-2 region proposal + classification
            # First detect all regions
            prompt = "<REGION_PROPOSAL>"
            inputs = self._processor(
                text=prompt, images=image, return_tensors="pt"
            ).to(self._device)

            import torch
            with torch.no_grad():
                generated_ids = self._model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=1024,
                    num_beams=3,
                    do_sample=False,
                )

            generated_text = self._processor.batch_decode(
                generated_ids, skip_special_tokens=False
            )[0]

            # Parse region proposals
            proposals = self._parse_region_proposals(generated_text)

            for idx, (bbox, _score) in enumerate(proposals):
                # Classify each region
                region_type, confidence = self._classify_single_region(image, bbox)
                regions.append(
                    RegionClassification(
                        region_type=region_type,
                        confidence=confidence,
                        bbox=bbox,
                    )
                )

        except Exception as exc:
            logger.error("Region classification failed: %s", exc, exc_info=True)

        logger.info("Classified %d regions", len(regions))
        return sorted(regions, key=lambda r: r.confidence, reverse=True)

    # ------------------------------------------------------------------
    # OCR with layout understanding
    # ------------------------------------------------------------------

    def _ocr_with_layout(self, image_path: str) -> Tuple[str, str]:
        """
        Perform OCR with layout understanding using Florence-2.

        Returns
        -------
        tuple[str, str]
            (full_text, layout_description)
        """
        if not self._ensure_model():
            return "", ""

        try:
            from PIL import Image as PILImage

            image = PILImage.open(image_path).convert("RGB")

            # Florence-2 OCR with region prompt
            prompt = "<OCR_WITH_REGION>"
            inputs = self._processor(
                text=prompt, images=image, return_tensors="pt"
            ).to(self._device)

            import torch
            with torch.no_grad():
                generated_ids = self._model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=2048,
                    num_beams=3,
                    do_sample=False,
                )

            generated_text = self._processor.batch_decode(
                generated_ids, skip_special_tokens=False
            )[0]

            # Parse OCR output
            full_text, layout_description = self._parse_ocr_output(generated_text)
            return full_text, layout_description

        except Exception as exc:
            logger.error("OCR with layout failed: %s", exc, exc_info=True)
            return "", ""

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def _ensure_model(self) -> bool:
        """Ensure Florence-2 model is loaded. Returns False if unavailable."""
        if self._model_loaded and self._model is not None:
            return True

        return self._load_model()

    def _load_model(self) -> bool:
        """Load Florence-2 model with lazy loading pattern."""
        if self._model_loaded:
            return self._model is not None

        try:
            import torch
            from transformers import (
                AutoModelForCausalLM,
                AutoProcessor,
            )

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(
                "Loading Florence-2 model (%s) on %s ...",
                self._model_name,
                self._device,
            )

            self._processor = AutoProcessor.from_pretrained(
                self._model_name,
                trust_remote_code=True,
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                self._model_name,
                trust_remote_code=True,
                torch_dtype=torch.float16 if self._device == "cuda" else torch.float32,
            ).to(self._device)

            self._model.eval()
            self._model_loaded = True
            self._florence_available = True
            logger.info("Florence-2 model loaded successfully")
            return True

        except ImportError as exc:
            self._model_loaded = False
            self._florence_available = False
            logger.error(
                "Transformers library not available for Florence-2: %s", exc
            )
            return False
        except Exception as exc:
            self._model_loaded = False
            logger.error("Failed to load Florence-2 model: %s", exc, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Output parsing helpers
    # ------------------------------------------------------------------

    def _parse_od_output(
        self, text: str, image_size: Tuple[int, int]
    ) -> List[DetectedObject]:
        """
        Parse Florence-2 object detection output.

        Florence-2 OD format:
        <loc_x1><loc_y1><loc_x2><loc_y2> label

        Locations are normalised to [0, 999].
        """
        objects: List[DetectedObject] = []

        # Remove special tokens and clean
        text = text.replace("<s>", "").replace("</s>", "")
        text = text.strip()

        # Parse location pairs
        import re
        pattern = r"<loc_(\d+)>\s*<loc_(\d+)>\s*<loc_(\d+)>\s*<loc_(\d+)>\s*([^\s<]+)"
        matches = re.findall(pattern, text)

        img_w, img_h = image_size

        for match in matches:
            x1, y1, x2, y2, label = match
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

            # Convert normalised coords [0, 999] to pixel coords
            px1 = int(x1 / 999 * img_w)
            py1 = int(y1 / 999 * img_h)
            px2 = int(x2 / 999 * img_w)
            py2 = int(y2 / 999 * img_h)

            area = (px2 - px1) * (py2 - py1)

            objects.append(
                DetectedObject(
                    label=label.strip(),
                    confidence=0.8,  # Florence-2 doesn't provide per-object confidence
                    bbox={"x1": px1, "y1": py1, "x2": px2, "y2": py2},
                    area=area,
                )
            )

        return objects

    def _parse_region_proposals(
        self, text: str
    ) -> List[Tuple[Dict[str, int], float]]:
        """Parse Florence-2 region proposal output."""
        proposals: List[Tuple[Dict[str, int], float]] = []

        text = text.replace("<s>", "").replace("</s>", "").strip()

        import re
        pattern = r"<loc_(\d+)>\s*<loc_(\d+)>\s*<loc_(\d+)>\s*<loc_(\d+)>"
        matches = re.findall(pattern, text)

        for match in matches:
            x1, y1, x2, y2 = [int(v) for v in match]
            proposals.append(
                ({"x1": x1, "y1": y1, "x2": x2, "y2": y2}, 0.8)
            )

        return proposals

    def _parse_ocr_output(self, text: str) -> Tuple[str, str]:
        """
        Parse Florence-2 OCR with region output.

        Extracts text and layout description.
        """
        text = text.replace("<s>", "").replace("</s>", "").strip()

        # Try to separate text content from location markers
        import re

        # Remove location markers for pure text
        clean_text = re.sub(r"<loc_\d+>", "", text)
        clean_text = " ".join(clean_text.split())

        # Build layout description from region markers
        layout_parts: List[str] = []
        loc_pattern = r"<loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>\s*(.*?)(?=<loc_\d+>|$)"
        loc_matches = re.findall(loc_pattern, text, re.DOTALL)

        for idx, match in enumerate(loc_matches):
            x1, y1, x2, y2, content = match
            content = content.strip().replace("<s>", "").replace("</s>", "")
            if content:
                layout_parts.append(f"Region {idx + 1} at ({x1},{y1})-({x2},{y2}): {content[:100]}")

        layout_description = "\n".join(layout_parts) if layout_parts else "No structured layout detected"

        return clean_text, layout_description

    def _classify_single_region(
        self,
        image,
        bbox: Dict[str, int],
    ) -> Tuple[str, float]:
        """Classify a single image region using Florence-2."""
        try:
            from PIL import Image as PILImage

            # Crop region from image
            x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
            # Convert normalised coords to pixel if needed
            img_w, img_h = image.size
            crop_x1 = int(x1 / 999 * img_w) if x1 <= 999 else x1
            crop_y1 = int(y1 / 999 * img_h) if y1 <= 999 else y1
            crop_x2 = int(x2 / 999 * img_w) if x2 <= 999 else x2
            crop_y2 = int(y2 / 999 * img_h) if y2 <= 999 else y2

            crop = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))

            prompt = "<CAPTION>"
            inputs = self._processor(
                text=prompt, images=crop, return_tensors="pt"
            ).to(self._device)

            import torch
            with torch.no_grad():
                generated_ids = self._model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=64,
                    num_beams=2,
                    do_sample=False,
                )

            caption = self._processor.batch_decode(
                generated_ids, skip_special_tokens=True
            )[0].lower()

            # Match caption against known region types
            best_type = "body"
            best_score = 0.0
            for region_type in _REGION_TYPES:
                if region_type in caption:
                    best_type = region_type
                    best_score = 0.85
                    break

            return best_type, best_score

        except Exception as exc:
            logger.debug("Single region classification failed: %s", exc)
            return "body", 0.3

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _get_image_dimensions(image_path: str) -> Optional[Tuple[int, int]]:
        """Get image (width, height) dimensions."""
        try:
            from PIL import Image as PILImage
            img = PILImage.open(image_path)
            return img.size
        except Exception:
            return None

    @staticmethod
    def _contains_arabic(text: str) -> bool:
        """Check if text contains Arabic characters."""
        return any("\u0600" <= c <= "\u06FF" for c in text)


# =============================================================================
# Singleton instance
# =============================================================================

medical_image_processor = MedicalImageProcessor()
