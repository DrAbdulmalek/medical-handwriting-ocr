"""
Equation Parser Module for Medical Handwriting OCR.

Detects and parses mathematical/chemical equations from medical document
images using Pix2Tex (LaTeX-OCR) for equation-to-LaTeX conversion.

Supports handwritten and printed equations commonly found in medical
prescriptions, dosage calculations, and clinical formulas.
"""

from __future__ import annotations

import io
import logging
import math
import uuid
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models
# =============================================================================


class EquationRegion(BaseModel):
    """Represents a detected equation region within an image."""

    region_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this equation region",
    )
    bbox: Dict[str, int] = Field(
        ...,
        description="Bounding box {x1, y1, x2, y2} in image coordinates",
    )
    latex: str = Field("", description="Recognised LaTeX string")
    confidence: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Recognition confidence (0–1)",
    )
    equation_type: str = Field(
        "math",
        description="Type classification: 'math', 'chemical', 'dosage', 'unknown'",
    )
    image_crop_path: Optional[str] = Field(
        None,
        description="Path to saved cropped image of the equation region",
    )
    is_handwritten: bool = Field(
        False,
        description="Whether the equation appears to be handwritten",
    )
    postprocessed_latex: Optional[str] = Field(
        None,
        description="LaTeX after post-processing corrections",
    )


# =============================================================================
# EquationParser
# =============================================================================


class EquationParser:
    """
    LaTeX equation detector and parser for medical document images.

    Uses Pix2Tex (LaTeX-OCR) model for converting equation images to
    LaTeX strings.  The model is lazily loaded on first use to avoid
    slow startup.

    Capabilities:
    * Detect equation regions in document images (via layout analysis)
    * Convert equation crops to LaTeX notation
    * Classify equations as math, chemical, dosage, or unknown
    * Post-process LaTeX output for common medical notation patterns

    Fallback: If Pix2Tex is unavailable the parser returns empty results
    with appropriate warnings.
    """

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._model_loaded: bool = False
        self._pix2tex_available: Optional[bool] = None
        self._cv2_available: Optional[bool] = None
        self._pil_available: Optional[bool] = None
        self._min_confidence: float = 0.3
        self._output_dir: str = str(settings.CROP_DIR) / "equations" if hasattr(settings.CROP_DIR, "__truediv__") else str(settings.CROP_DIR)
        import os
        os.makedirs(self._output_dir, exist_ok=True)
        logger.info("EquationParser initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_equations(self, image_path: str) -> List[EquationRegion]:
        """
        Detect and parse all equation regions in an image.

        Parameters
        ----------
        image_path : str
            Path to the input image (PNG, JPEG, TIFF, etc.).

        Returns
        -------
        list[EquationRegion]
            List of detected equation regions with LaTeX output.
        """
        logger.info("Detecting equations in image: %s", image_path)

        regions: List[EquationRegion] = []

        try:
            # Step 1: Detect potential equation regions using layout analysis
            bboxes = self._detect_equation_bboxes(image_path)
            logger.info("Found %d potential equation regions", len(bboxes))

            if not bboxes:
                logger.info("No equation regions detected")
                return regions

            # Step 2: Load model lazily
            if not self._model_loaded:
                self._load_model()

            # Step 3: Parse each region
            for bbox in bboxes:
                try:
                    equation = self._parse_region(image_path, bbox)
                    if equation and equation.confidence >= self._min_confidence:
                        regions.append(equation)
                except Exception as exc:
                    logger.debug("Failed to parse equation region %s: %s", bbox, exc)

        except Exception as exc:
            logger.error("Equation detection failed for %s: %s", image_path, exc, exc_info=True)

        logger.info("Detected %d equations", len(regions))
        return regions

    def parse_equation_to_latex(self, image_crop_path: str) -> Optional[EquationRegion]:
        """
        Parse a cropped equation image to LaTeX.

        Parameters
        ----------
        image_crop_path : str
            Path to an image containing only the equation.

        Returns
        -------
        EquationRegion or None
            Parsed equation with LaTeX string, or None on failure.
        """
        try:
            if not self._model_loaded:
                self._load_model()

            return self._recognise_equation(image_crop_path, bbox={"x1": 0, "y1": 0, "x2": 0, "y2": 0})

        except Exception as exc:
            logger.error("Failed to parse equation from %s: %s", image_crop_path, exc)
            return None

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        """Lazily load the Pix2Tex model and processor."""
        if self._model_loaded:
            return

        try:
            from pix2tex.cli import LatexOCR
            self._model = LatexOCR()
            self._model_loaded = True
            logger.info("Pix2Tex (LaTeX-OCR) model loaded successfully")
        except ImportError:
            self._model_loaded = False
            logger.warning(
                "pix2tex is not installed. "
                "Equation recognition will not be available. "
                "Install with: pip install pix2tex"
            )
            raise
        except Exception as exc:
            self._model_loaded = False
            logger.error("Failed to load Pix2Tex model: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Region detection
    # ------------------------------------------------------------------

    def _detect_equation_bboxes(self, image_path: str) -> List[Dict[str, int]]:
        """
        Detect bounding boxes of potential equation regions.

        Uses heuristic analysis based on:
        * Aspect ratio (equations tend to be wider than tall)
        * Contour density
        * Presence of mathematical symbols
        * Spatial separation from text blocks
        """
        bboxes: List[Dict[str, int]] = []

        if not self._check_cv2():
            logger.warning("OpenCV not available for equation region detection")
            return bboxes

        try:
            import cv2
            import numpy as np

            img = cv2.imread(image_path)
            if img is None:
                logger.error("Failed to read image: %s", image_path)
                return bboxes

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

            # Remove noise
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

            # Find contours
            contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            img_h, img_w = img.shape[:2]
            min_area = (img_w * img_h) * 0.0005
            max_area = (img_w * img_h) * 0.5

            # Group nearby contours into potential equation regions
            regions: List[Tuple[int, int, int, int]] = []

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = w * h

                if area < min_area or area > max_area:
                    continue

                # Equation-like aspect ratio: width > height (horizontal orientation)
                aspect_ratio = w / max(h, 1)
                if aspect_ratio < 0.3 or aspect_ratio > 20:
                    continue

                regions.append((x, y, x + w, y + h))

            # Merge overlapping / nearby regions
            merged = self._merge_regions(regions, merge_distance=20)

            # Filter: keep regions that look like equations
            for (x1, y1, x2, y2) in merged:
                w = x2 - x1
                h = y2 - y1
                area = w * h

                # Check for mathematical symbols in the region
                region_img = binary[y1:y2, x1:x2]
                has_math_symbols = self._has_math_symbols(region_img)

                if has_math_symbols or (w > 80 and h > 20 and area > min_area * 3):
                    bboxes.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})

        except Exception as exc:
            logger.warning("Equation bbox detection failed: %s", exc)

        return bboxes

    def _merge_regions(
        self,
        regions: List[Tuple[int, int, int, int]],
        merge_distance: int = 20,
    ) -> List[Tuple[int, int, int, int]]:
        """Merge overlapping or nearby bounding box regions."""
        if not regions:
            return []

        # Sort by x1 coordinate
        regions = sorted(regions, key=lambda r: r[0])
        merged: List[Tuple[int, int, int, int]] = [regions[0]]

        for region in regions[1:]:
            prev = merged[-1]
            # Check if regions overlap or are within merge_distance
            if region[0] <= prev[2] + merge_distance and region[1] <= prev[3] + merge_distance:
                merged[-1] = (
                    min(prev[0], region[0]),
                    min(prev[1], region[1]),
                    max(prev[2], region[2]),
                    max(prev[3], region[3]),
                )
            else:
                merged.append(region)

        return merged

    def _has_math_symbols(self, binary_crop) -> bool:  # type: ignore[no-untyped-def]
        """Heuristic check for mathematical symbols in a binary image region."""
        import numpy as np

        # Count small components (dots, operators like +, -, =, ×)
        try:
            import cv2
            num_labels, _ = cv2.connectedComponents(binary_crop)
            # Many small connected components suggest mathematical operators
            return 3 < num_labels < 50
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Equation recognition
    # ------------------------------------------------------------------

    def _parse_region(
        self, image_path: str, bbox: Dict[str, int]
    ) -> Optional[EquationRegion]:
        """
        Crop an equation region from the image and recognise its LaTeX.

        Parameters
        ----------
        image_path : str
            Path to the source image.
        bbox : dict
            Bounding box with ``x1``, ``y1``, ``x2``, ``y2`` keys.

        Returns
        -------
        EquationRegion or None
        """
        import os

        x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]

        # Save the crop
        crop_path = os.path.join(
            self._output_dir,
            f"{uuid.uuid4().hex}.png",
        )

        try:
            from PIL import Image as PILImage

            img = PILImage.open(image_path).convert("RGB")
            crop = img.crop((x1, y1, x2, y2))
            crop.save(crop_path)
        except Exception as exc:
            logger.debug("Failed to crop equation region: %s", exc)
            return None

        return self._recognise_equation(crop_path, bbox=bbox, crop_path=crop_path)

    def _recognise_equation(
        self,
        image_path: str,
        bbox: Dict[str, int],
        crop_path: Optional[str] = None,
    ) -> Optional[EquationRegion]:
        """Recognise the LaTeX content of an equation image using Pix2Tex."""
        if not self._model_loaded or self._model is None:
            return EquationRegion(
                bbox=bbox,
                latex="",
                confidence=0.0,
                image_crop_path=crop_path,
            )

        try:
            from PIL import Image as PILImage

            img = PILImage.open(image_path).convert("RGB")
            latex_str = self._model(img)

            if isinstance(latex_str, bytes):
                latex_str = latex_str.decode("utf-8", errors="replace")

            # Clean up the LaTeX
            latex_str = self._clean_latex(latex_str)

            # Post-process for medical context
            postprocessed = self._postprocess_medical_latex(latex_str)

            # Estimate confidence based on output quality
            confidence = self._estimate_confidence(latex_str)

            # Classify equation type
            eq_type = self._classify_equation(latex_str)

            # Estimate if handwritten
            is_handwritten = self._estimate_handwritten(image_path)

            return EquationRegion(
                bbox=bbox,
                latex=latex_str,
                confidence=confidence,
                equation_type=eq_type,
                image_crop_path=crop_path or image_path,
                is_handwritten=is_handwritten,
                postprocessed_latex=postprocessed,
            )

        except Exception as exc:
            logger.warning("Equation recognition failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_latex(latex: str) -> str:
        """Clean and normalise LaTeX output."""
        if not latex:
            return ""
        # Remove excessive whitespace
        latex = " ".join(latex.split())
        # Remove common Pix2Tex artefacts
        latex = latex.replace("\\mathrm", "")
        latex = latex.replace("\\operatorname", "")
        latex = latex.replace("_{\\", "_{")
        # Ensure matching braces
        open_count = latex.count("{")
        close_count = latex.count("}")
        if open_count > close_count:
            latex += "}" * (open_count - close_count)
        elif close_count > open_count:
            latex = "{" * (close_count - open_count) + latex
        return latex.strip()

    def _postprocess_medical_latex(self, latex: str) -> str:
        """
        Apply medical-specific LaTeX post-processing.

        Handles common patterns found in medical documents:
        * Dosage notation (e.g., mg/kg/day → \\text{mg/kg/day})
        * Chemical formulas
        * Unit notation
        """
        if not latex:
            return ""

        # Dosage patterns: mg, kg, ml, mcg, μg, etc.
        dosage_units = ["mg", "kg", "ml", "mcg", "μg", "g", "l", "IU", "units"]
        for unit in dosage_units:
            # Replace plain unit text with \text{} for readability
            import re
            latex = re.sub(rf"\\text\s*\{{\s*{unit}\s*\}}", f"\\text{{{unit}}}", latex)
            latex = re.sub(rf"(?<!\\text\{{){unit}(?!}})", f"\\text{{{unit}}}", latex)

        # Common chemical subscripts
        chemical_subs = {
            "CO2": "CO_{2}",
            "H2O": "H_{2}O",
            "O2": "O_{2}",
            "H2": "H_{2}",
            "NaCl": "NaCl",
        }
        for formula, replacement in chemical_subs.items():
            if formula in latex:
                latex = latex.replace(formula, replacement)

        return latex

    @staticmethod
    def _classify_equation(latex: str) -> str:
        """Classify the type of equation based on LaTeX content."""
        if not latex:
            return "unknown"

        latex_lower = latex.lower()

        # Chemical formula indicators
        chemical_indicators = ["_{", "H_{2}O", "CO_{2}", "O_{2}", "Na", "Cl", "mg", "mol"]
        if any(ind in latex for ind in chemical_indicators):
            return "chemical"

        # Dosage indicators
        dosage_indicators = ["mg", "kg", "ml", "dose", "per day", "daily", "bid", "tid", "qid"]
        if any(ind in latex_lower for ind in dosage_indicators):
            return "dosage"

        # Mathematical indicators
        math_indicators = ["frac", "sqrt", "sum", "int", "pmatrix", "left(", "right("]
        if any(ind in latex_lower for ind in math_indicators):
            return "math"

        return "math"

    @staticmethod
    def _estimate_confidence(latex: str) -> float:
        """
        Estimate recognition confidence based on LaTeX output quality.

        Heuristics:
        * Non-empty output: base confidence 0.7
        * Well-formed LaTeX (balanced braces): +0.1
        * Contains known LaTeX commands: +0.1
        * No unknown characters: +0.1
        """
        if not latex:
            return 0.0

        confidence = 0.7

        # Check balanced braces
        if latex.count("{") == latex.count("}"):
            confidence += 0.1

        # Check for known LaTeX commands
        known_commands = {"frac", "sqrt", "sum", "int", "text", "pmatrix", "alpha", "beta", "gamma", "mu", "sigma"}
        if any(f"\\{cmd}" in latex for cmd in known_commands):
            confidence += 0.1

        # Check for unknown/problematic patterns
        problematic = ["???", "!!!", "###", "\uFFFD"]
        if not any(p in latex for p in problematic):
            confidence += 0.1

        return min(1.0, confidence)

    def _estimate_handwritten(self, image_path: str) -> bool:
        """
        Estimate whether the equation in the image is handwritten.

        Uses simple heuristic based on stroke irregularity analysis.
        """
        if not self._check_cv2():
            return False

        try:
            import cv2
            import numpy as np

            img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                return False

            _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # Measure stroke irregularity using standard deviation of
            # connected component sizes
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
                cv2.bitwise_not(binary), connectivity=8
            )

            if num_labels < 3:
                return False

            areas = stats[1:, cv2.CC_STAT_AREA]
            if len(areas) == 0:
                return False

            # High variance in component size suggests handwriting
            area_std = float(np.std(areas))
            area_mean = float(np.mean(areas))
            coefficient_of_variation = area_std / max(area_mean, 1)

            return coefficient_of_variation > 1.5

        except Exception as exc:
            logger.debug("Handwriting estimation failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Availability checks
    # ------------------------------------------------------------------

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

equation_parser = EquationParser()
