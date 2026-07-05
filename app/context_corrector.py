"""
Context-Aware OCR Post-Processor for Arabic Medical Text.

Provides lightweight, CPU-friendly correction that runs AFTER the ensemble
voting step.  It is designed to complement (not replace) the existing
dictionary correction in ocr_engine.py.

Layers:
1. ArabicTextNormalizer  — unify letter forms, strip tashkeel
2. ContextCorrector      — fuzzy-match against medical dictionary,
                            merge fragmented Arabic words (PaddleOCR issue),
                            apply simple context rules (dosage format, etc.)

All corrections are gated by the ENABLE_CONTEXT_CORRECTION env-var (default: 1).
"""

import json
import logging
import os
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------
ENABLE_CONTEXT_CORRECTION = os.environ.get("ENABLE_CONTEXT_CORRECTION", "1") == "1"

# ---------------------------------------------------------------------------
# Layer 1 — Arabic Text Normalizer
# ---------------------------------------------------------------------------

# Common Arabic ligatures that PaddleOCR may output as isolated forms
_LIGATURE_MAP = {
    "\uFEFB": "لا",   # ﻻ  → لا
    "\uFEF7": "لأ",   # ﻷ  → لأ
    "\uFEF9": "لإ",   # ﻹ  → لإ
    "\uFEF5": "لآ",   # ﻵ  → لآ
    "\uFDF2": "الله",  # ﷲ  → الله
}

# Presentation-Form-A/B → standard Arabic (supplement to NFKC)
# These are forms that NFKC may not fully decompose
_EXTRA_FORM_MAP = {
    "\uFB50": "ا",  # ARABIC LETTER ALEF WASLA ISOLATED FORM
    "\uFB51": "ا",
    "\uFE80": "ا",  # ARABIC LETTER ALEF WITH HAMZA ABOVE ISOLATED FORM
    "\uFE81": "ا",  # ARABIC LETTER ALEF WITH HAMZA BELOW ISOLATED FORM
    "\uFE83": "ا",  # ARABIC LETTER ALEF WITH MADDA ABOVE ISOLATED FORM
    "\uFE85": "ا",  # ARABIC LETTER WAW WITH HAMZA ABOVE ISOLATED FORM
    "\uFE87": "و",  # ARABIC LETTER WAW WITH HAMZA ABOVE
    "\uFE8D": "ب",  # ARABIC LETTER BEH ISOLATED FORM → ب
    "\uFE8F": "ت",  # ARABIC LETTER TEH
    "\uFE93": "ث",  # ARABIC LETTER THEH
    "\uFE97": "ج",  # ARABIC LETTER JEEM
    "\uFE9B": "ح",  # ARABIC LETTER HAH
    "\uFE9F": "خ",  # ARABIC LETTER KHAH
    "\uFEA3": "د",  # ARABIC LETTER DAL
    "\uFEA5": "ذ",  # ARABIC LETTER THAL
    "\uFEA7": "ر",  # ARABIC LETTER REH
    "\uFEA9": "ز",  # ARABIC LETTER ZAIN
    "\uFEAB": "س",  # ARABIC LETTER SEEN
    "\uFEAD": "ش",  # ARABIC LETTER SHEEN
    "\uFEAF": "ص",  # ARABIC LETTER SAD
    "\uFEB1": "ض",  # ARABIC LETTER DAD
    "\uFEB5": "ط",  # ARABIC LETTER TAH
    "\uFEB9": "ظ",  # ARABIC LETTER ZAH
    "\uFEBD": "ع",  # ARABIC LETTER AIN
    "\uFEC1": "غ",  # ARABIC LETTER GHAIN
    "\uFEC5": "ف",  # ARABIC LETTER FEH
    "\uFEC9": "ق",  # ARABIC LETTER QAF
    "\uFECD": "ك",  # ARABIC LETTER KAF
    "\uFED1": "ل",  # ARABIC LETTER LAM
    "\uFED5": "م",  # ARABIC LETTER MEEM
    "\uFED9": "ن",  # ARABIC LETTER NOON
    "\uFEDD": "ه",  # ARABIC LETTER HEH
    "\uFEE1": "و",  # ARABIC LETTER WAW
    "\uFEE5": "ي",  # ARABIC LETTER YEH
}

# Tashkeel range (diacritics) — always strip for matching
_TASHKEEL_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E8\u06EA-\u06ED]")

# Arabic letter range
_ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")


class ArabicTextNormalizer:
    """Normalize Arabic text for consistent fuzzy matching."""

    # Unify hamza forms, taa marbuta, etc.
    CHAR_MAP = {
        "إ": "ا", "أ": "ا", "آ": "ا", "ٱ": "ا",
        "ة": "ه",
        "ى": "ي",
        "ؤ": "و",
        "ئ": "ي",
    }

    @classmethod
    def normalize(cls, text: str) -> str:
        """Full normalization: strip tashkeel, map variants, remove extra spaces."""
        if not text:
            return text
        # Strip tashkeel
        text = _TASHKEEL_RE.sub("", text)
        # Map ligatures first
        for lig, repl in _LIGATURE_MAP.items():
            text = text.replace(lig, repl)
        # Map presentation forms
        for form_char, repl in _EXTRA_FORM_MAP.items():
            text = text.replace(form_char, repl)
        # Map variant characters
        for old, new in cls.CHAR_MAP.items():
            text = text.replace(old, new)
        # Clean spaces
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @classmethod
    def is_arabic(cls, text: str) -> bool:
        return bool(_ARABIC_CHAR_RE.search(text)) if text else False


# ---------------------------------------------------------------------------
# Layer 2 — Context-Aware Corrector
# ---------------------------------------------------------------------------

# Simple context rules for common medical text patterns
_CONTEXT_RULES = [
    {
        "pattern": r"(\d+)\s*(مل|mg|مجم)\b",
        "replacement": r"\1 \2",
        "desc": "Dosage format",
    },
    {
        "pattern": r"(\d+)\s*(مرات?|مرة)\s*(يوم[يًاً]?|صباحاً|مساءً)",
        "replacement": r"\1 \2 \3",
        "desc": "Frequency format",
    },
]


class ContextCorrector:
    """Post-OCR corrector using medical dictionary fuzzy matching.

    Designed to be *lightweight* (no ML model, no GPU).  It runs after the
    ensemble voting step and corrects individual region texts.
    """

    def __init__(self, dictionary_terms: Optional[Set[str]] = None):
        """Initialise with optional pre-loaded dictionary terms."""
        self.dictionary: Set[str] = set()
        self.normalized_dict: Dict[str, str] = {}  # normalized → original
        if dictionary_terms:
            self.load_dictionary(dictionary_terms)

    def load_dictionary(self, terms):
        """Load medical terms (set or list of strings)."""
        if isinstance(terms, (list, tuple)):
            self.dictionary = set(terms)
        else:
            self.dictionary = terms
        # Build normalized lookup
        self.normalized_dict = {}
        for term in self.dictionary:
            if term and len(term) >= 2:
                norm = ArabicTextNormalizer.normalize(term)
                if norm and norm not in self.normalized_dict:
                    self.normalized_dict[norm] = term
        logger.info("ContextCorrector: loaded %d terms", len(self.normalized_dict))

    def fuzzy_match(self, word: str, threshold: float = 0.75) -> Optional[str]:
        """Find the closest match in the medical dictionary.

        Uses SequenceMatcher (stdlib) — no external dependency needed.
        Falls back to rapidfuzz.ratio if available (faster for large dicts).
        """
        if not word or not self.normalized_dict:
            return None

        norm_word = ArabicTextNormalizer.normalize(word)
        if not norm_word or len(norm_word) < 2:
            return None

        # Direct lookup
        if norm_word in self.normalized_dict:
            return self.normalized_dict[norm_word]

        # Fuzzy search
        best_match = None
        best_ratio = 0.0

        # Use rapidfuzz if available (much faster for large dictionaries)
        try:
            from rapidfuzz import fuzz
            for norm_term, original in self.normalized_dict.items():
                # Quick length filter to skip obviously wrong lengths
                if abs(len(norm_term) - len(norm_word)) > 3:
                    continue
                ratio = fuzz.ratio(norm_word, norm_term)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = original
        except ImportError:
            for norm_term, original in self.normalized_dict.items():
                if abs(len(norm_term) - len(norm_word)) > 3:
                    continue
                ratio = SequenceMatcher(None, norm_word, norm_term).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = original

        if best_ratio >= threshold:
            logger.debug("Context fuzzy: '%s' → '%s' (%.0f%%)", word, best_match, best_ratio * 100)
            return best_match
        return None

    def merge_fragmented_words(self, words: List[str]) -> List[str]:
        """Merge short fragmented Arabic words (common PaddleOCR issue).

        Strategies:
        1. Merge 2-3 consecutive short Arabic words and fuzzy-match
        2. Skip numbers/symbols (never merge them)
        3. Try fuzzy match on individual words too

        E.g., ['ا', 'ل', 'محتويات'] → ['المحتويات']
        Or:   ['محتو', 'يات'] → ['محتويات']  (if 'محتويات' is in dictionary)
        """
        if not words:
            return words

        merged: List[str] = []
        i = 0

        while i < len(words):
            word = words[i]
            norm_word = ArabicTextNormalizer.normalize(word)

            # Skip empty words
            if not word.strip():
                i += 1
                continue

            # Skip numbers, symbols, and pure ASCII (never merge these)
            if re.match(r'^[\d\.\-\+\(\)\s]+$', norm_word) or not ArabicTextNormalizer.is_arabic(norm_word):
                merged.append(word)
                i += 1
                continue

            # Strategy 1: Try merging short word (<=3 chars) with next 1-2 words
            if len(norm_word) <= 3 and i + 1 < len(words):
                next_word = words[i + 1]
                norm_next = ArabicTextNormalizer.normalize(next_word)

                # Try binary merge
                combined = norm_word + norm_next
                corrected = self.fuzzy_match(combined, threshold=0.65)
                if corrected:
                    merged.append(corrected)
                    i += 2
                    continue

                # Try ternary merge (3 words)
                if i + 2 < len(words):
                    third_word = words[i + 2]
                    norm_third = ArabicTextNormalizer.normalize(third_word)
                    combined3 = norm_word + norm_next + norm_third
                    corrected3 = self.fuzzy_match(combined3, threshold=0.60)
                    if corrected3:
                        merged.append(corrected3)
                        i += 3
                        continue

            # Strategy 2: Try merging with previous short word
            if merged and len(ArabicTextNormalizer.normalize(merged[-1])) <= 3 and ArabicTextNormalizer.is_arabic(norm_word):
                combined = ArabicTextNormalizer.normalize(merged[-1]) + norm_word
                corrected = self.fuzzy_match(combined, threshold=0.65)
                if corrected:
                    merged[-1] = corrected
                    i += 1
                    continue

            # Strategy 3: Fuzzy match individual word against dictionary
            corrected_single = self.fuzzy_match(norm_word, threshold=0.75)
            if corrected_single:
                merged.append(corrected_single)
            else:
                merged.append(word)

            i += 1

        return merged

    def detect_and_merge_fragments(self, text: str) -> str:
        """كشف ودمج الحروف العربية المتقطعة بشكل ذكي.

        This is a higher-level entry point that:
        1. Normalizes text (presentation forms → standard Arabic)
        2. Splits into words
        3. Detects if text has Arabic fragmented patterns
        4. Tries merging all words into a single term first
        5. Falls back to word-by-word merge_fragmented_words()
        """
        if not text:
            return text

        # Normalize text first
        normalized = ArabicTextNormalizer.normalize(text)
        words = normalized.split()

        if not words:
            return text

        # If all words are short Arabic, try concatenating all and fuzzy-matching
        # (useful for PaddleOCR output where a word is split into presentation forms)
        all_arabic_short = all(
            ArabicTextNormalizer.is_arabic(w) and len(ArabicTextNormalizer.normalize(w)) <= 4
            for w in words
        )

        if all_arabic_short and len(words) <= 6:
            combined_all = ''.join(ArabicTextNormalizer.normalize(w) for w in words)
            corrected_all = self.fuzzy_match(combined_all, threshold=0.55)
            if corrected_all:
                return corrected_all

        # Also try with space-joined (for multi-word terms)
        if len(words) <= 5:
            combined_space = ' '.join(words)
            corrected_space = self.fuzzy_match(combined_space, threshold=0.55)
            if corrected_space:
                return corrected_space

        # Fall back to word-level merging
        return ' '.join(self.merge_fragmented_words(words))

    def correct_text(self, text: str) -> Tuple[str, bool]:
        """Correct a single OCR text line.

        Pipeline:
        1. Normalize text (strip tashkeel, presentation forms)
        2. Apply context rules (regex-based formatting)
        3. Try whole-line fuzzy match against dictionary
        4. Try fragment merging + per-word correction
        5. Per-word fuzzy correction for remaining words

        Returns (corrected_text, was_corrected).
        """
        if not text:
            return text, False

        original = text

        # Step 0: Normalize presentation forms first
        text = ArabicTextNormalizer.normalize(text)

        # Step 1: Apply context rules (regex-based formatting)
        for rule in _CONTEXT_RULES:
            text = re.sub(rule["pattern"], rule["replacement"], text)

        # Step 2: Try fuzzy match the whole line against dictionary
        if self.normalized_dict:
            corrected = self.fuzzy_match(text, threshold=0.80)
            if corrected:
                return corrected, True

        # Step 3: Detect and merge fragmented Arabic words
        merged_text = self.detect_and_merge_fragments(text)
        if merged_text != text and self.normalized_dict:
            # If merging produced a different text, validate against dictionary
            merged_corrected = self.fuzzy_match(merged_text, threshold=0.75)
            if merged_corrected:
                return merged_corrected, True
            # Accept the merged text even if not in dictionary
            return merged_text, True

        # Step 4: Per-word fuzzy correction for remaining words
        if not self.normalized_dict:
            return text, (text != original)

        words = text.split()
        final_words = []
        for word in words:
            if ArabicTextNormalizer.is_arabic(word) and len(word) >= 3:
                word_corrected = self.fuzzy_match(word, threshold=0.80)
                if word_corrected:
                    final_words.append(word_corrected)
                else:
                    final_words.append(word)
            else:
                final_words.append(word)

        result = " ".join(final_words)
        return result, (result != original)


# ---------------------------------------------------------------------------
# Singleton + public API
# ---------------------------------------------------------------------------

_corrector: Optional[ContextCorrector] = None


def _ensure_corrector() -> Optional[ContextCorrector]:
    """Lazy-initialize the corrector, loading terms from the existing dictionary + orthopedic terms."""
    global _corrector
    if _corrector is not None:
        return _corrector

    # Import the existing dictionary from ocr_engine
    try:
        import app.ocr_engine as _eng
        _eng._ensure_dictionary_loaded()
        if _eng._dictionary_terms:
            _corrector = ContextCorrector(_eng._dictionary_terms)
            logger.info("ContextCorrector initialized with %d dictionary terms",
                        len(_eng._dictionary_terms))

            # Also inject orthopedic terms for fragment merging to find them
            ortho_path = Path("/app/medical_terms_dict.json")
            if ortho_path.exists():
                try:
                    import json as _json
                    with open(ortho_path, 'r', encoding='utf-8') as f:
                        data = _json.load(f)
                    ortho_terms = set()
                    for term, variants in data.get("orthopedic_terms", {}).items():
                        ortho_terms.add(term)
                        if isinstance(variants, list):
                            ortho_terms.update(v for v in variants if v)
                    _corrector.load_dictionary(ortho_terms)
                    logger.info("ContextCorrector: added %d orthopedic terms", len(ortho_terms))
                except Exception as e:
                    logger.warning("Failed to load orthopedic terms into ContextCorrector: %s", e)

            return _corrector
    except Exception as exc:
        logger.warning("Could not load dictionary for ContextCorrector: %s", exc)

    # Fallback: try loading just orthopedic terms
    ortho_path = Path("/app/medical_terms_dict.json")
    if ortho_path.exists():
        try:
            import json as _json
            with open(ortho_path, 'r', encoding='utf-8') as f:
                data = _json.load(f)
            ortho_terms = set()
            for term, variants in data.get("orthopedic_terms", {}).items():
                ortho_terms.add(term)
                if isinstance(variants, list):
                    ortho_terms.update(v for v in variants if v)
            _corrector = ContextCorrector(ortho_terms)
            logger.info("ContextCorrector: initialized with %d orthopedic terms only", len(ortho_terms))
            return _corrector
        except Exception as exc:
            logger.warning("Failed to load orthopedic terms: %s", exc)

    # Last fallback: empty corrector (will only apply context rules)
    _corrector = ContextCorrector()
    return _corrector


def context_correct(text: str) -> Tuple[str, bool]:
    """Correct OCR text using context-aware dictionary matching.

    Safe to call on every region — returns original text if no correction found
    or if the corrector is disabled/unavailable.

    Returns (corrected_text, was_corrected).
    """
    if not ENABLE_CONTEXT_CORRECTION or not text:
        return text, False

    corrector = _ensure_corrector()
    if corrector is None:
        return text, False

    try:
        return corrector.correct_text(text)
    except Exception as exc:
        logger.warning("context_correct failed: %s", exc)
        return text, False