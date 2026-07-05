"""
Medical Object Detector Module for Medical Handwriting OCR.

Provides medical-domain-specific object detection for identifying
key elements in medical documents such as prescription headers,
dosage instructions, doctor signatures, stamps, patient info blocks,
and vital signs areas.

Uses Florence-2 or YOLO as the detection backend, with fallback
heuristic detection when models are unavailable.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models
# =============================================================================


class MedicalStamp(BaseModel):
    """Detected medical stamp or seal."""

    stamp_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    bbox: Dict[str, int] = Field(..., description="Bounding box {x1, y1, x2, y2}")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    stamp_type: str = Field(
        "unknown",
        description="Type: 'hospital_seal', 'doctor_stamp', 'official_seal', 'pharmacy_seal'",
    )
    text_content: Optional[str] = Field(None, description="OCR text within the stamp")


class DoctorSignature(BaseModel):
    """Detected doctor signature region."""

    signature_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    bbox: Dict[str, int] = Field(...)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    is_handwritten: bool = Field(True)
    name_nearby: Optional[str] = Field(None, description="Doctor name found near signature")


class PrescriptionBlock(BaseModel):
    """Structured prescription information extracted from a document."""

    prescription_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    header_bbox: Optional[Dict[str, int]] = Field(None, description="Header region bbox")
    doctor_name: Optional[str] = Field(None, description="Detected doctor name")
    doctor_license: Optional[str] = Field(None, description="Doctor license number")
    patient_name: Optional[str] = Field(None, description="Detected patient name")
    patient_id: Optional[str] = Field(None, description="Patient ID / file number")
    date: Optional[str] = Field(None, description="Prescription date")
    drugs: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of detected drug entries with name, dosage, frequency, duration",
    )
    diagnosis: Optional[str] = Field(None, description="Detected diagnosis text")
    notes: Optional[str] = Field(None, description="Additional notes")
    signature: Optional[DoctorSignature] = Field(None, description="Detected signature")
    stamps: List[MedicalStamp] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Overall block confidence")


class VitalSignsArea(BaseModel):
    """Detected vital signs section in a medical document."""

    area_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    bbox: Dict[str, int] = Field(...)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    vitals_text: str = Field("", description="Raw text of vital signs")
    parsed_vitals: Dict[str, str] = Field(
        default_factory=dict,
        description="Parsed key-value pairs (e.g. {'BP': '120/80', 'HR': '72'})",
    )


class MedicalElements(BaseModel):
    """All detected medical-specific elements in a document image."""

    image_path: str = Field("")
    prescription_headers: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Detected prescription header regions",
    )
    dosage_instructions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Detected dosage instruction regions",
    )
    drug_names: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Detected drug name regions",
    )
    stamps: List[MedicalStamp] = Field(default_factory=list)
    signatures: List[DoctorSignature] = Field(default_factory=list)
    patient_info_blocks: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Detected patient information regions",
    )
    vital_signs_areas: List[VitalSignsArea] = Field(default_factory=list)
    prescription_block: Optional[PrescriptionBlock] = Field(None)
    processing_time_ms: float = Field(0.0)
    warnings: List[str] = Field(default_factory=list)


# =============================================================================
# MedicalObjectDetector
# =============================================================================


class MedicalObjectDetector:
    """
    Medical-domain-specific object detector.

    Detects key medical document elements:
    * Prescription headers
    * Dosage instructions
    * Drug names
    * Medical stamps / seals
    * Doctor signatures
    * Patient information blocks
    * Vital signs areas

    Uses Florence-2 for deep-learning detection with OpenCV-based
    heuristic fallbacks.
    """

    def __init__(self) -> None:
        self._processor: Any = None
        self._model: Any = None
        self._device: Optional[str] = None
        self._model_loaded: bool = False
        self._cv2_available: Optional[bool] = None
        self._pil_available: Optional[bool] = None
        self._model_name: str = "microsoft/Florence-2-large"
        self._output_dir: str = str(settings.CROP_DIR)
        os.makedirs(self._output_dir, exist_ok=True)
        logger.info("MedicalObjectDetector initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_medical_elements(self, image_path: str) -> MedicalElements:
        """
        Detect all medical-specific elements in an image.

        Parameters
        ----------
        image_path : str
            Path to the medical document image.

        Returns
        -------
        MedicalElements
            All detected medical elements.
        """
        import time

        start = time.perf_counter()
        result = MedicalElements(image_path=image_path)

        try:
            # Use Florence-2 if available for detection
            if self._ensure_model():
                result = self._detect_with_florence(image_path, result)
            else:
                logger.info("Florence-2 unavailable; using heuristic detection")
                result = self._detect_heuristic(image_path, result)

            # Post-processing: extract prescription block if enough elements found
            if (
                result.prescription_headers
                or result.drug_names
                or result.dosage_instructions
            ):
                result.prescription_block = self._extract_prescription_block_from_result(result)

        except Exception as exc:
            logger.error("Medical element detection failed: %s", exc, exc_info=True)
            result.warnings.append(f"Detection error: {exc}")

        result.processing_time_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Medical elements detected: %d headers, %d drugs, %d signatures, %d stamps, %.1fms",
            len(result.prescription_headers),
            len(result.drug_names),
            len(result.signatures),
            len(result.stamps),
            result.processing_time_ms,
        )
        return result

    def extract_prescription_block(self, image_path: str) -> PrescriptionBlock:
        """
        Extract structured prescription information from an image.

        Parameters
        ----------
        image_path : str
            Path to the prescription image.

        Returns
        -------
        PrescriptionBlock
            Structured prescription data.
        """
        elements = self.detect_medical_elements(image_path)
        return elements.prescription_block or PrescriptionBlock()

    # ------------------------------------------------------------------
    # Florence-2 detection
    # ------------------------------------------------------------------

    def _detect_with_florence(
        self, image_path: str, result: MedicalElements
    ) -> MedicalElements:
        """Detect medical elements using Florence-2 model."""
        try:
            from PIL import Image as PILImage

            image = PILImage.open(image_path).convert("RGB")

            # Custom prompt for medical document detection
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
                )

            generated_text = self._processor.batch_decode(
                generated_ids, skip_special_tokens=False
            )[0]

            # Parse and categorise detections
            objects = self._parse_florence_od(generated_text, image.size)

            for obj in objects:
                category = self._categorise_medical_object(obj["label"], obj["bbox"])
                self._add_to_result(category, obj, result)

        except Exception as exc:
            logger.warning("Florence-2 detection failed: %s", exc)
            result.warnings.append(f"Florence-2 error: {exc}")

        return result

    def _parse_florence_od(
        self, text: str, image_size: Tuple[int, int]
    ) -> List[Dict[str, Any]]:
        """Parse Florence-2 object detection output."""
        objects: List[Dict[str, Any]] = []

        text = text.replace("<s>", "").replace("</s>", "").strip()
        pattern = r"<loc_(\d+)>\s*<loc_(\d+)>\s*<loc_(\d+)>\s*<loc_(\d+)>\s*([^\s<]+)"
        matches = re.findall(pattern, text)

        img_w, img_h = image_size

        for match in matches:
            x1, y1, x2, y2, label = match
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

            px1 = int(x1 / 999 * img_w)
            py1 = int(y1 / 999 * img_h)
            px2 = int(x2 / 999 * img_w)
            py2 = int(y2 / 999 * img_h)

            objects.append({
                "label": label.strip().lower(),
                "bbox": {"x1": px1, "y1": py1, "x2": px2, "y2": py2},
                "confidence": 0.8,
            })

        return objects

    @staticmethod
    def _categorise_medical_object(
        label: str, bbox: Dict[str, int]
    ) -> str:
        """Categorise a detected object label into a medical element type."""
        label_lower = label.lower()

        # Prescription-related
        if any(kw in label_lower for kw in ["header", "title", "heading"]):
            return "prescription_header"
        if any(kw in label_lower for kw in ["drug", "medicine", "medication"]):
            return "drug_name"
        if any(kw in label_lower for kw in ["dosage", "dose", "mg", "ml"]):
            return "dosage_instruction"
        if any(kw in label_lower for kw in ["stamp", "seal"]):
            return "stamp"
        if any(kw in label_lower for kw in ["signature", "sign"]):
            return "signature"
        if any(kw in label_lower for kw in ["patient", "name"]):
            return "patient_info"
        if any(kw in label_lower for kw in ["vital", "bp", "blood pressure", "heart"]):
            return "vital_signs"

        return "unknown"

    def _add_to_result(
        self,
        category: str,
        obj: Dict[str, Any],
        result: MedicalElements,
    ) -> None:
        """Add a detected object to the appropriate result list."""
        bbox = obj["bbox"]
        confidence = obj["confidence"]
        label = obj["label"]

        entry = {
            "label": label,
            "bbox": bbox,
            "confidence": confidence,
        }

        if category == "prescription_header":
            result.prescription_headers.append(entry)
        elif category == "drug_name":
            result.drug_names.append(entry)
        elif category == "dosage_instruction":
            result.dosage_instructions.append(entry)
        elif category == "stamp":
            result.stamps.append(
                MedicalStamp(
                    bbox=bbox,
                    confidence=confidence,
                    stamp_type="medical_stamp",
                )
            )
        elif category == "signature":
            result.signatures.append(
                DoctorSignature(
                    bbox=bbox,
                    confidence=confidence,
                    is_handwritten=True,
                )
            )
        elif category == "patient_info":
            result.patient_info_blocks.append(entry)
        elif category == "vital_signs":
            result.vital_signs_areas.append(
                VitalSignsArea(
                    bbox=bbox,
                    confidence=confidence,
                )
            )

    # ------------------------------------------------------------------
    # Heuristic fallback detection
    # ------------------------------------------------------------------

    def _detect_heuristic(
        self, image_path: str, result: MedicalElements
    ) -> MedicalElements:
        """
        Detect medical elements using OpenCV heuristics.

        This fallback uses:
        * Text density analysis for finding headers
        * Ink density for signatures and stamps
        * Spatial analysis for layout-based detection
        """
        if not self._check_cv2():
            result.warnings.append("OpenCV not available for heuristic detection")
            return result

        try:
            import cv2
            import numpy as np

            img = cv2.imread(image_path)
            if img is None:
                logger.error("Failed to read image: %s", image_path)
                return result

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            img_h, img_w = img.shape[:2]
            binary = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY_INV, 11, 2
            )

            # Detect high-density ink regions (likely stamps/signatures)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            dilated = cv2.dilate(binary, kernel, iterations=2)
            _, contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = w * h
                img_area = img_w * img_h

                # Skip very small or very large regions
                if area < img_area * 0.005 or area > img_area * 0.4:
                    continue

                # Analyse the region's ink density
                roi = binary[y : y + h, x : x + w]
                ink_density = float(np.count_nonzero(roi)) / max(roi.size, 1)

                bbox = {"x1": x, "y1": y, "x2": x + w, "y2": y + h}

                # High ink density + compact shape → likely a stamp
                if ink_density > 0.3 and 0.5 < (w / max(h, 1)) < 2.0:
                    result.stamps.append(
                        MedicalStamp(
                            bbox=bbox,
                            confidence=min(ink_density, 0.9),
                            stamp_type="medical_stamp",
                        )
                    )

                # Medium density, elongated or irregular → likely signature
                elif 0.1 < ink_density < 0.35 and h < w * 2:
                    result.signatures.append(
                        DoctorSignature(
                            bbox=bbox,
                            confidence=min(ink_density * 2, 0.7),
                            is_handwritten=True,
                        )
                    )

            # Detect top-of-page headers (text in upper 20% of image)
            header_region = binary[: int(img_h * 0.2), :]
            header_density = float(np.count_nonzero(header_region)) / max(header_region.size, 1)

            if header_density > 0.02:
                result.prescription_headers.append({
                    "label": "header_region",
                    "bbox": {"x1": 0, "y1": 0, "x2": img_w, "y2": int(img_h * 0.2)},
                    "confidence": min(header_density * 5, 0.7),
                })

            # Detect bottom region (likely patient info / footer)
            footer_region = binary[int(img_h * 0.8):, :]
            footer_density = float(np.count_nonzero(footer_region)) / max(footer_region.size, 1)

            if footer_density > 0.02:
                result.patient_info_blocks.append({
                    "label": "patient_info_region",
                    "bbox": {"x1": 0, "y1": int(img_h * 0.8), "x2": img_w, "y2": img_h},
                    "confidence": min(footer_density * 5, 0.6),
                })

        except Exception as exc:
            logger.warning("Heuristic detection failed: %s", exc)
            result.warnings.append(f"Heuristic detection error: {exc}")

        return result

    # ------------------------------------------------------------------
    # Prescription block extraction
    # ------------------------------------------------------------------

    def _extract_prescription_block_from_result(
        self, result: MedicalElements
    ) -> PrescriptionBlock:
        """
        Assemble a PrescriptionBlock from individually detected elements.
        """
        block = PrescriptionBlock()

        # Extract header bbox
        if result.prescription_headers:
            block.header_bbox = result.prescription_headers[0]["bbox"]
            block.confidence = result.prescription_headers[0]["confidence"]

        # Extract stamps
        block.stamps = result.stamps

        # Extract signature
        if result.signatures:
            block.signature = result.signatures[0]

        # Build drug list from detected drug names and dosage instructions
        drug_entries: List[Dict[str, Any]] = []
        for drug in result.drug_names:
            entry: Dict[str, Any] = {
                "name": drug["label"],
                "name_bbox": drug["bbox"],
                "name_confidence": drug["confidence"],
            }

            # Try to find nearby dosage instruction
            for dosage in result.dosage_instructions:
                if self._is_nearby(drug["bbox"], dosage["bbox"], max_distance=200):
                    entry["dosage"] = dosage["label"]
                    entry["dosage_bbox"] = dosage["bbox"]

            drug_entries.append(entry)

        block.drugs = drug_entries

        # Compute overall confidence
        confidences = [
            block.confidence,
            *(s.confidence for s in block.stamps),
            *(d.get("name_confidence", 0.5) for d in block.drugs),
        ]
        if confidences:
            block.confidence = sum(confidences) / len(confidences)

        return block

    @staticmethod
    def _is_nearby(
        bbox1: Dict[str, int],
        bbox2: Dict[str, int],
        max_distance: int = 200,
    ) -> bool:
        """Check if two bounding boxes are nearby (within max_distance pixels)."""
        cx1 = (bbox1["x1"] + bbox1["x2"]) // 2
        cy1 = (bbox1["y1"] + bbox1["y2"]) // 2
        cx2 = (bbox2["x1"] + bbox2["x2"]) // 2
        cy2 = (bbox2["y1"] + bbox2["y2"]) // 2

        distance = ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5
        return distance <= max_distance

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def _ensure_model(self) -> bool:
        """Ensure the Florence-2 model is loaded."""
        if self._model_loaded and self._model is not None:
            return True
        return self._load_model()

    def _load_model(self) -> bool:
        """Load Florence-2 model (lazy loading)."""
        if self._model_loaded:
            return self._model is not None

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoProcessor

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info("Loading Florence-2 for medical detection on %s ...", self._device)

            self._processor = AutoProcessor.from_pretrained(
                self._model_name, trust_remote_code=True
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                self._model_name,
                trust_remote_code=True,
                torch_dtype=torch.float16 if self._device == "cuda" else torch.float32,
            ).to(self._device)
            self._model.eval()
            self._model_loaded = True
            logger.info("Florence-2 loaded for medical detection")
            return True

        except ImportError:
            self._model_loaded = False
            logger.warning("transformers not available for Florence-2 medical detection")
            return False
        except Exception as exc:
            self._model_loaded = False
            logger.error("Failed to load Florence-2 for medical detection: %s", exc)
            return False

    def _check_cv2(self) -> bool:
        """Check if OpenCV is available."""
        if self._cv2_available is None:
            try:
                import cv2  # noqa: F401
                self._cv2_available = True
            except ImportError:
                self._cv2_available = False
        return self._cv2_available


# =============================================================================
# Singleton instance
# =============================================================================

medical_object_detector = MedicalObjectDetector()
