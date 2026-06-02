"""Integration bridge with medical-ocr-postprocessor package.

This module provides a seamless integration between the handwriting OCR pipeline
and the medical-ocr-postprocessor library for text correction and PHI masking.
The postprocessor runs AFTER the OCR engine produces raw text, providing:
  - Dictionary-based OCR correction (exact + fuzzy + phrase matching)
  - PHI detection and masking (7 types x 3 modes)
  - Human review candidate identification

Usage:
    from app.postprocessor_integration import PostprocessorBridge
    
    bridge = PostprocessorBridge()
    corrected_text, corrections = bridge.correct(raw_ocr_text)
    masked_text, phi_detections = bridge.mask_phi(text, mode="tag")
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PostprocessorBridge:
    """Bridge between handwriting OCR pipeline and medical-ocr-postprocessor.
    
    Wraps the postprocessor package with lazy initialization and graceful
    fallback when the package is not installed or dictionary is unavailable.
    """

    def __init__(self, dictionary_path: Optional[str] = None, config_path: Optional[str] = None):
        self._dictionary_path = dictionary_path
        self._config_path = config_path
        self._corrector = None
        self._available = False
        self._init_error: Optional[str] = None

    def _ensure_initialized(self) -> bool:
        """Lazy-initialize the postprocessor corrector."""
        if self._corrector is not None:
            return True

        try:
            from medical_ocr_toolkit import MedicalOCRCorrector

            kwargs = {}
            if self._dictionary_path:
                kwargs["dictionary_path"] = self._dictionary_path
            if self._config_path:
                kwargs["config_path"] = self._config_path

            self._corrector = MedicalOCRCorrector(**kwargs)
            self._available = True
            logger.info("Postprocessor bridge initialized successfully")
            return True

        except ImportError:
            self._init_error = "medical-ocr-postprocessor not installed"
            logger.warning("Postprocessor not available — install with: pip install medical-ocr-postprocessor")
            return False
        except Exception as exc:
            self._init_error = str(exc)
            logger.error(f"Postprocessor initialization failed: {exc}")
            return False

    @property
    def available(self) -> bool:
        """Check if the postprocessor is ready to use."""
        return self._ensure_initialized()

    @property
    def initialization_error(self) -> Optional[str]:
        """Get the initialization error message, if any."""
        return self._init_error

    def correct(
        self,
        text: str,
        phi_mask: bool = False,
        confidence_threshold: float = 0.85,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Correct OCR text using the postprocessor engine.

        Falls back to returning the original text if the postprocessor
        is not available.

        Args:
            text: Raw OCR text to correct
            phi_mask: Whether to detect/mask PHI before correction
            confidence_threshold: Minimum confidence for fuzzy corrections

        Returns:
            Tuple of (corrected_text, corrections_list)
        """
        if not text:
            return "", []

        if not self._ensure_initialized():
            logger.debug("Postprocessor unavailable, returning raw text")
            return text, []

        try:
            corrected, corrections = self._corrector.correct_text(
                text, phi_mask=phi_mask
            )
            logger.info(
                "Postprocessor correction applied",
                original_length=len(text),
                corrections=len(corrections),
            )
            return corrected, corrections

        except Exception as exc:
            logger.error(f"Postprocessor correction failed: {exc}")
            return text, []

    def mask_phi(
        self,
        text: str,
        mode: str = "tag",
    ) -> tuple[str, list[dict[str, Any]]]:
        """Detect and mask PHI in text.

        Args:
            text: Text to scan for PHI
            mode: One of "tag", "mask", "remove"

        Returns:
            Tuple of (masked_text, phi_detections)
        """
        if not text:
            return "", []

        try:
            from medical_ocr_toolkit import mask_phi as _mask_phi

            masked, detections = _mask_phi(text, mode=mode)
            return masked, detections

        except ImportError:
            logger.warning("PHI masking unavailable")
            return text, []
        except Exception as exc:
            logger.error(f"PHI masking failed: {exc}")
            return text, []

    def review_candidates(
        self,
        min_confidence: float = 0.7,
    ) -> dict[str, Any]:
        """Get corrections that need human review.

        Args:
            min_confidence: Max confidence threshold for review candidates

        Returns:
            Dict with 'candidates' list and 'stats'
        """
        try:
            from medical_ocr_toolkit import review_candidates as _review

            return _review(min_confidence=min_confidence)

        except ImportError:
            return {"candidates": [], "stats": {}}
        except Exception as exc:
            logger.error(f"Review candidates failed: {exc}")
            return {"candidates": [], "stats": {}}

    def stats(self) -> dict[str, Any]:
        """Get postprocessor statistics."""
        if not self._ensure_initialized():
            return {"available": False, "error": self._init_error}

        try:
            return self._corrector.stats()
        except Exception as exc:
            return {"available": False, "error": str(exc)}

    def close(self) -> None:
        """Release resources."""
        if self._corrector is not None:
            try:
                self._corrector.close()
            except Exception:
                pass
            self._corrector = None
        logger.info("Postprocessor bridge closed")

    def __enter__(self) -> "PostprocessorBridge":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# Singleton bridge instance
_bridge: Optional[PostprocessorBridge] = None


def get_postprocessor_bridge(
    dictionary_path: Optional[str] = None,
    config_path: Optional[str] = None,
) -> PostprocessorBridge:
    """Get or create the global postprocessor bridge instance.

    Args:
        dictionary_path: Optional path to correction dictionary CSV
        config_path: Optional path to config YAML

    Returns:
        PostprocessorBridge singleton instance
    """
    global _bridge
    if _bridge is None:
        _bridge = PostprocessorBridge(
            dictionary_path=dictionary_path,
            config_path=config_path,
        )
    return _bridge
