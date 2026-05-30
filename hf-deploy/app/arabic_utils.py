"""
Arabic text utility functions for proper RTL display.

PaddleOCR returns Arabic text in logical (storage) order.  When that text is
rendered in a UI that does not apply the Unicode Bidirectional Algorithm
(e.g. plain HTML, Gradio markdown), the characters appear left-to-right
visually — which is incorrect for Arabic (a right-to-left script).

This module provides:
- ``fix_arabic_text()``: reshape + bidi-display for correct rendering
- ``reverse_arabic_text()``: simple character reversal (lightweight fallback)
- ``is_arabic()``: detect whether a string contains Arabic characters
"""

import logging
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy imports so that missing optional packages don't break the whole app.
_arabic_reshaper = None
_bidi = None


def _get_reshaper():
    """Lazy-load ``arabic_reshaper``."""
    global _arabic_reshaper
    if _arabic_reshaper is None:
        try:
            import arabic_reshaper as ar
            _arabic_reshaper = ar
            logger.info("arabic_reshaper loaded successfully")
        except ImportError:
            logger.warning(
                "arabic_reshaper not installed — Arabic reshaping will be skipped. "
                "Install with: pip install arabic-reshaper"
            )
    return _arabic_reshaper


def _get_bidi():
    """Lazy-load ``python-bidi``."""
    global _bidi
    if _bidi is None:
        try:
            from bidi.algorithm import get_display
            _bidi = get_display
            logger.info("python-bidi loaded successfully")
        except ImportError:
            logger.warning(
                "python-bidi not installed — BiDi algorithm will not be applied. "
                "Install with: pip install python-bidi"
            )
    return _bidi


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_arabic(text: str) -> bool:
    """Return ``True`` if *text* contains any Arabic character (U+0600–U+06FF)."""
    if not text:
        return False
    return any('\u0600' <= c <= '\u06FF' for c in text)


def fix_arabic_text(text: str) -> str:
    """Fix Arabic text for correct visual display.

    1. **Reshape** — connect isolated Arabic letters using ``arabic_reshaper``.
    2. **Reorder** — apply the Unicode BiDi algorithm via ``get_display()``
       so the logical order is converted to visual (display) order.

    If the optional dependencies are unavailable the text is returned unchanged.

    Parameters
    ----------
    text: str
        The raw OCR output (may contain Arabic, Latin, numbers, or mixed).

    Returns
    -------
    str
        Text ready for correct RTL rendering.
    """
    if not text or not is_arabic(text):
        return text

    # Step 1: Reshape Arabic letters for proper ligature/connection rendering
    reshaper = _get_reshaper()
    reshaped = text
    if reshaper is not None:
        try:
            reshaped = reshaper.reshape(text)
        except Exception as exc:
            logger.warning("arabic_reshaper.reshape() failed: %s", exc)

    # Step 2: Apply BiDi algorithm to convert logical → visual order
    get_display = _get_bidi()
    if get_display is not None:
        try:
            reshaped = get_display(reshaped)
        except Exception as exc:
            logger.warning("bidi.algorithm.get_display() failed: %s", exc)

    return reshaped


def reverse_arabic_text(text: str) -> str:
    """Simple character-level reversal for Arabic text.

    This is a lightweight fallback when ``python-bidi`` is unavailable.
    It reverses only Arabic words while preserving non-Arabic tokens.

    Parameters
    ----------
    text: str
        Raw OCR output.

    Returns
    -------
    str
        Text with Arabic words reversed.
    """
    if not text or not is_arabic(text):
        return text

    # Split into segments of Arabic vs non-Arabic characters
    segments: list = []
    current = ""
    current_is_arabic = False

    for ch in text:
        ch_is_arabic = '\u0600' <= ch <= '\u06FF'
        if ch_is_arabic != current_is_arabic:
            if current:
                segments.append((current, current_is_arabic))
            current = ch
            current_is_arabic = ch_is_arabic
        else:
            current += ch

    if current:
        segments.append((current, current_is_arabic))

    # Reverse only the Arabic segments
    result = ""
    for segment_text, segment_is_arabic in segments:
        if segment_is_arabic:
            # Reverse the word characters
            result += segment_text[::-1]
        else:
            result += segment_text

    return result
