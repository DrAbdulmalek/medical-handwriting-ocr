"""
Multi-Engine OCR for Medical Handwriting Recognition.

Engines (lazy-loaded to save memory):
- PaddleOCR (Arabic + English) — primary detection + recognition
- Tesseract OCR (Arabic + English) — secondary
- EasyOCR (Arabic + English) — secondary
- TrOCR (Handwritten) — for handwritten text crops

Provides ensemble voting-based text selection with corrections database
support for improving future predictions.
"""

import base64
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image as PILImage

from app.arabic_utils import fix_arabic_text
from app.database import get_stats, lookup_correction, save_correction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy-loaded engine singletons
# ---------------------------------------------------------------------------

_paddle_ocr = None
_tesseract_available: Optional[bool] = None
_easyocr_reader = None
_trocr_processor = None
_trocr_model = None


def _init_paddle():
    global _paddle_ocr
    if _paddle_ocr is None:
        from paddleocr import PaddleOCR
        _paddle_ocr = PaddleOCR(use_angle_cls=True, lang="ar")
        logger.info("PaddleOCR initialized (CPU)")
    return _paddle_ocr


def _init_tesseract() -> bool:
    global _tesseract_available
    if _tesseract_available is None:
        try:
            import pytesseract  # noqa: F401
            _tesseract_available = True
            logger.info("Tesseract OCR available")
        except ImportError:
            _tesseract_available = False
            logger.warning("pytesseract not installed — Tesseract disabled")
    return _tesseract_available


def _init_easyocr():
    global _easyocr_reader
    if _easyocr_reader is None:
        try:
            import easyocr
            _easyocr_reader = easyocr.Reader(["ar", "en"], gpu=False, verbose=False)
            logger.info("EasyOCR initialized")
        except Exception as exc:
            logger.warning("EasyOCR init failed: %s", exc)
    return _easyocr_reader


def _init_trocr():
    global _trocr_processor, _trocr_model
    if _trocr_processor is None:
        try:
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
            _trocr_processor = TrOCRProcessor.from_pretrained(
                "microsoft/trocr-base-handwritten"
            )
            _trocr_model = VisionEncoderDecoderModel.from_pretrained(
                "microsoft/trocr-base-handwritten"
            )
            logger.info("TrOCR initialized")
        except Exception as exc:
            logger.warning("TrOCR init failed: %s", exc)
    return _trocr_processor, _trocr_model


# ---------------------------------------------------------------------------
# Per-engine runners
# ---------------------------------------------------------------------------


def _run_paddle_full(image_path: str) -> List[Dict]:
    """Run PaddleOCR on the full image and return raw regions."""
    paddle = _init_paddle()
    result = paddle.ocr(image_path, cls=True)

    regions: List[Dict] = []
    if result and result[0]:
        for idx, line in enumerate(result[0]):
            if not line:
                continue
            bbox_pts = line[0]
            text = line[1][0] if len(line) > 1 else ""
            confidence = line[1][1] if len(line) > 1 else 0.0

            x_coords = [p[0] for p in bbox_pts]
            y_coords = [p[1] for p in bbox_pts]

            regions.append({
                "bbox": {
                    "x1": int(min(x_coords)),
                    "y1": int(min(y_coords)),
                    "x2": int(max(x_coords)),
                    "y2": int(max(y_coords)),
                },
                "raw_text": text,
                "confidence": float(confidence),
            })
    return regions


def _run_tesseract_crop(crop_bgr: np.ndarray) -> Tuple[str, float]:
    """Run Tesseract on a crop. Returns (text, confidence 0-1)."""
    try:
        import pytesseract
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        pil = PILImage.fromarray(rgb)
        text = pytesseract.image_to_string(pil, lang="ara+eng", config="--psm 8").strip()
        data = pytesseract.image_to_data(
            pil, lang="ara+eng", config="--psm 8", output_type=pytesseract.Output.DICT
        )
        max_conf = 0
        for c in data.get("conf", []):
            try:
                v = int(c)
                if v > max_conf:
                    max_conf = v
            except (ValueError, TypeError):
                pass
        return text, max_conf / 100.0
    except Exception as exc:
        logger.warning("Tesseract crop failed: %s", exc)
        return "", 0.0


def _run_easyocr_crop(crop_bgr: np.ndarray) -> Tuple[str, float]:
    """Run EasyOCR on a crop. Returns (text, confidence 0-1)."""
    reader = _init_easyocr()
    if reader is None:
        return "", 0.0
    try:
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        results = reader.readtext(rgb)
        if results:
            texts = [r[1] for r in results]
            confs = [r[2] for r in results]
            return " ".join(texts), max(confs)
    except Exception as exc:
        logger.warning("EasyOCR crop failed: %s", exc)
    return "", 0.0


def _run_trocr_crop(crop_bgr: np.ndarray) -> Tuple[str, float]:
    """Run TrOCR on a crop. Returns (text, confidence 0-1)."""
    proc, model = _init_trocr()
    if proc is None or model is None:
        return "", 0.0
    try:
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        pil = PILImage.fromarray(rgb)
        pixel_values = proc(pil, return_tensors="pt").pixel_values
        generated = model.generate(pixel_values)
        text = proc.batch_decode(generated, skip_special_tokens=True)[0].strip()
        return text, 0.75  # TrOCR doesn't expose per-token confidence easily
    except Exception as exc:
        logger.warning("TrOCR crop failed: %s", exc)
    return "", 0.0


# ---------------------------------------------------------------------------
# Ensemble / voting
# ---------------------------------------------------------------------------


def _select_best(all_texts: Dict[str, Tuple[str, float]]) -> Tuple[str, float, str]:
    """Select the best text from multiple engines using fuzzy voting.

    Returns (best_text, best_confidence, best_engine).
    """
    non_empty = {k: v for k, v in all_texts.items() if v[0].strip()}
    if not non_empty:
        return "", 0.0, "none"
    if len(non_empty) == 1:
        engine = next(iter(non_empty))
        return non_empty[engine][0], non_empty[engine][1], engine

    try:
        from rapidfuzz import fuzz

        # Group similar texts
        groups: List[Dict] = []
        for engine, (text, conf) in non_empty.items():
            matched = False
            for g in groups:
                if any(fuzz.ratio(text, t) > 70 for t in g["texts"]):
                    g["texts"].append(text)
                    g["engines"].append(engine)
                    g["confs"].append(conf)
                    g["votes"] += 1
                    matched = True
                    break
            if not matched:
                groups.append({"texts": [text], "engines": [engine], "confs": [conf], "votes": 1})

        best_group = max(groups, key=lambda g: (g["votes"], max(g["confs"])))
        best_text = best_group["texts"][0]
        best_engine = best_group["engines"][0]
        best_conf = max(best_group["confs"])
        return best_text, best_conf, best_engine
    except ImportError:
        # Fallback: pick highest confidence
        best_engine = max(non_empty, key=lambda k: non_empty[k][1])
        return non_empty[best_engine][0], non_empty[best_engine][1], best_engine


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------


def detect_regions_multi(image_path: str, padding: int = 10) -> List[Dict]:
    """Run all available OCR engines and return merged results with crops.

    Workflow:
    1. PaddleOCR detects bounding boxes on the full image
    2. Each crop is fed to Tesseract, EasyOCR, TrOCR
    3. Ensemble voting picks the best text
    4. Corrections database is consulted for known patterns
    """
    t0 = time.time()

    # Step 1: PaddleOCR regions
    paddle_regions = _run_paddle_full(image_path)
    if not paddle_regions:
        return []

    img = cv2.imread(image_path)
    if img is None:
        logger.error("Cannot read image: %s", image_path)
        return []

    h, w = img.shape[:2]

    # Pre-initialise lightweight engines
    _init_tesseract()
    _init_easyocr()

    results: List[Dict] = []

    for idx, preg in enumerate(paddle_regions):
        bbox = preg["bbox"]
        x1 = max(0, bbox["x1"] - padding)
        y1 = max(0, bbox["y1"] - padding)
        x2 = min(w, bbox["x2"] + padding)
        y2 = min(h, bbox["y2"] + padding)
        crop = img[y1:y2, x1:x2]

        # Encode crop
        _, buf = cv2.imencode(".png", crop)
        crop_b64 = base64.b64encode(buf).decode("utf-8")

        # Collect texts from all engines
        all_texts: Dict[str, Tuple[str, float]] = {
            "paddle": (preg["raw_text"], preg["confidence"]),
        }

        if _tesseract_available:
            t, c = _run_tesseract_crop(crop)
            if t:
                all_texts["tesseract"] = (t, c)

        if _easyocr_reader is not None:
            t, c = _run_easyocr_crop(crop)
            if t:
                all_texts["easyocr"] = (t, c)

        # NOTE: TrOCR disabled — model download is ~1.5GB and only supports English
        # Uncomment below to re-enable for low-confidence English text:
        # if preg["confidence"] < 0.80:
        #     t, c = _run_trocr_crop(crop)
        #     if t:
        #         all_texts["trocr"] = (t, c)

        # Ensemble voting
        best_raw, best_conf, best_engine = _select_best(all_texts)

        # Fix Arabic for display
        best_display = fix_arabic_text(best_raw)

        # Check corrections DB
        db_correction = lookup_correction(best_raw)
        final_display = fix_arabic_text(db_correction) if db_correction else best_display
        from_db = db_correction is not None

        results.append({
            "bbox": bbox,
            "raw_text": best_raw,
            "predicted_text": final_display,
            "all_texts": {k: fix_arabic_text(v[0]) for k, v in all_texts.items()},
            "all_raw_texts": {k: v[0] for k, v in all_texts.items()},
            "all_confidences": {k: v[1] for k, v in all_texts.items()},
            "best_engine": best_engine,
            "confidence": best_conf,
            "reading_order": idx,
            "crop_base64": crop_b64,
            "from_db": from_db,
            "db_correction": db_correction,
        })

    elapsed = time.time() - t0
    logger.info(
        "Multi-engine OCR done: %d regions in %.1fs (Paddle/Tesseract/EasyOCR/TrOCR)",
        len(results), elapsed,
    )
    return results


def get_engine_status() -> Dict:
    """Return availability status of each OCR engine."""
    status: Dict = {}

    try:
        _init_paddle()
        status["paddleocr"] = "ready"
    except Exception:
        status["paddleocr"] = "error"

    status["tesseract"] = "ready" if _init_tesseract() else "not available"

    reader = _init_easyocr()
    status["easyocr"] = "ready" if reader else "error"

    try:
        _init_trocr()
        status["trocr"] = "ready" if _trocr_model else "not available"
    except Exception:
        status["trocr"] = "not available"

    status["corrections_db"] = get_stats()
    return status


# Legacy singleton alias for backward compatibility
class OCREngine:
    """Thin wrapper for backward compatibility with single-engine usage."""

    def detect_regions(self, image_path: str) -> List[Dict]:
        regions = detect_regions_multi(image_path)
        return [
            {
                "bbox": r["bbox"],
                "predicted_text": r["predicted_text"],
                "confidence": r["confidence"],
                "reading_order": r["reading_order"],
            }
            for r in regions
        ]

    def detect_regions_with_crops(self, image_path: str, padding: int = 10) -> List[Dict]:
        return detect_regions_multi(image_path, padding=padding)

    @staticmethod
    def classify_script(text: str) -> str:
        has_arabic = any("\u0600" <= c <= "\u06FF" for c in text)
        has_latin = any(c.isascii() and c.isalpha() for c in text)
        if has_arabic and has_latin:
            return "mixed"
        if has_arabic:
            return "arabic"
        if has_latin:
            return "latin"
        return "numeric"


ocr_engine = OCREngine()
