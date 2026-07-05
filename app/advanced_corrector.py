"""
Advanced OCR Correction Pipeline
1. User Learning Engine: يتعلم من التصحيحات اليدوية ويحدث القاموس تلقائياً
2. Medical Context Processor: يفهم سياقات الأدوية، الجرعات، والتشخيصات
3. NLP Arabic Corrector: تصحيح سياقي خفيف باستخدام نموذج لغة عربي
"""
import re
import json
import os
import logging
from pathlib import Path
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)


# ==============================================================================
# 1. محرك التعلم من التصحيحات اليدوية
# ==============================================================================
class UserLearningEngine:
    """Learns from user manual corrections and builds an auto-updating dictionary.

    Every time the user edits a word and clicks 'Save Corrections', the wrong->correct
    mapping is stored with a count and context history.  On future OCR runs the engine
    checks the user dictionary first (highest priority) and applies fuzzy fallback.

    Integrates with ``sync_user_dict`` so that corrections survive Space restarts:
    they are uploaded to a private Hugging Face Dataset after every save and
    re-downloaded on startup.
    """

    def __init__(self, storage_path: str = "/data/user_corrections.json"):
        self.path = Path(storage_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.dictionary: Dict[str, dict] = {}
        self._sync = None
        self._load()
        self._init_sync()

    # ------------------------------------------------------------------
    def _init_sync(self):
        """Initialise cloud sync (non-fatal if huggingface_hub is missing)."""
        try:
            from app.sync_user_dict import get_sync
            self._sync = get_sync()
            # If local dict is empty, try downloading from HF
            if not self.dictionary:
                if self._sync.download():
                    self._load()
        except Exception as exc:
            logger.warning("Cloud sync init skipped: %s", exc)

    # ------------------------------------------------------------------
    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Handle both plain-dict and metadata-wrapped formats
                if isinstance(data, dict) and "corrections" in data:
                    self.dictionary = data["corrections"]
                else:
                    self.dictionary = data
                logger.info("UserLearningEngine: loaded %d corrections", len(self.dictionary))
            except Exception as e:
                logger.error("Failed to load user dictionary: %s", e)

    def save(self):
        """Save locally and trigger cloud upload."""
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.dictionary, f, ensure_ascii=False, indent=2)
            # Upload to HF in background (non-blocking, non-fatal)
            if self._sync and self._sync.available:
                try:
                    from app.sync_user_dict import sync_after_learning
                    sync_after_learning()
                except Exception as exc:
                    logger.warning("Cloud sync after save failed (local OK): %s", exc)
        except Exception as e:
            logger.error("Failed to save user dictionary: %s", e)

    # ------------------------------------------------------------------
    def learn(self, wrong_text: str, correct_text: str, context: str = ""):
        """Save a manual correction and update the dictionary."""
        wrong_norm = self._normalize(wrong_text)
        correct_norm = self._normalize(correct_text)
        if wrong_norm == correct_norm:
            return

        if wrong_norm not in self.dictionary:
            self.dictionary[wrong_norm] = {
                "correct": correct_norm,
                "count": 0,
                "contexts": [],
                "last_seen": "",
            }

        entry = self.dictionary[wrong_norm]
        entry["count"] += 1
        if context and context not in entry["contexts"]:
            entry["contexts"].append(context)
        entry["last_seen"] = datetime.now().isoformat()
        self.save()
        logger.info("Learned: '%s' -> '%s' (count: %d)", wrong_norm, correct_norm, entry["count"])

    # ------------------------------------------------------------------
    def get_correction(self, text: str) -> Optional[str]:
        """Look up a saved correction (exact or fuzzy fallback)."""
        norm = self._normalize(text)
        if norm in self.dictionary:
            return self.dictionary[norm]["correct"]

        # Fuzzy fallback — only for short words to keep it fast
        if len(norm) > 20:
            return None
        best_match = None
        best_ratio = 0.82
        for wrong, data in self.dictionary.items():
            if abs(len(wrong) - len(norm)) > 3:
                continue
            ratio = SequenceMatcher(None, norm, wrong).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = data["correct"]
        return best_match

    # ------------------------------------------------------------------
    def get_stats(self) -> Dict:
        """Return dictionary statistics for display."""
        total = len(self.dictionary)
        total_uses = sum(e.get("count", 0) for e in self.dictionary.values())
        return {
            "total_entries": total,
            "total_uses": total_uses,
            "cloud_sync": self._sync.available if self._sync else False,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(text: str) -> str:
        text = re.sub(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E8\u06EA-\u06ED]', '', text)
        text = text.replace('\u0640', '')  # tatweel
        for old, new in [('إ', 'ا'), ('أ', 'ا'), ('آ', 'ا'), ('ٱ', 'ا')]:
            text = text.replace(old, new)
        text = text.replace('ة', 'ه').replace('ى', 'ي').replace('ؤ', 'و').replace('ئ', 'ي')
        return text.strip()


# ==============================================================================
# 2. معالج السياق الطبي (أدوية، جرعات، تشخيصات)
# ==============================================================================
class MedicalContextProcessor:
    """Detects and corrects medical entities: drugs, dosages, diagnoses, orthopedic terms.

    Uses a configurable list of known drugs/diagnoses plus regex patterns for
    dosage formatting (e.g. ``500 mg`` → ``500 مجم``).
    Also loads a specialized orthopedic terms dictionary with variant mappings.
    """

    def __init__(self, config_path: str = "/data/medical_contexts.json",
                 terms_path: str = "/app/medical_terms_dict.json"):
        self.config_path = Path(config_path)
        self.terms_path = Path(terms_path)
        self.drugs: set = set()
        self.dosage_units = {"مجم", "mg", "مل", "ml", "قرص", "كبسولة", "أمبول", "قطرة"}
        self.diagnoses: set = set()
        self.orthopedic_terms: Dict[str, list] = {}
        self.term_variants: Dict[str, str] = {}  # normalized variant → correct term
        self._load_config()
        self._load_orthopedic_terms()

    def _load_orthopedic_terms(self):
        """تحميل قاموس المصطلحات العظمية المتخصصة"""
        if self.terms_path.exists():
            try:
                with open(self.terms_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                ortho_terms = data.get("orthopedic_terms", {})
                for correct_term, variants in ortho_terms.items():
                    if not isinstance(variants, list):
                        continue
                    self.orthopedic_terms[correct_term] = variants
                    # بناء قاموس عكسي للبحث السريع
                    for variant in variants:
                        norm_variant = self._normalize(variant)
                        self.term_variants[norm_variant] = correct_term

                logger.info("Orthopedic: loaded %d terms, %d variants",
                            len(self.orthopedic_terms), len(self.term_variants))
            except Exception as e:
                logger.error("Failed to load orthopedic terms: %s", e)
        else:
            logger.warning("Orthopedic terms file not found: %s", self.terms_path)

    @staticmethod
    def _normalize(text: str) -> str:
        """تطبيع النص للبحث"""
        text = re.sub(r'[\u0610-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E8\u06EA-\u06ED]', '', text)
        text = text.replace('\u0640', '')  # tatweel
        text = text.replace('ة', 'ه').replace('ى', 'ي').replace('ؤ', 'و').replace('ئ', 'ي')
        for old, new in [('إ', 'ا'), ('أ', 'ا'), ('آ', 'ا'), ('ٱ', 'ا')]:
            text = text.replace(old, new)
        return text.strip()

    def _load_config(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                self.drugs = set(cfg.get("drugs", []))
                self.diagnoses = set(cfg.get("diagnoses", []))
                logger.info("MedicalContext: %d drugs, %d diagnoses",
                            len(self.drugs), len(self.diagnoses))
                return
            except Exception as e:
                logger.warning("Failed to load medical context config: %s", e)

        # Default minimal built-in list
        self.drugs = {
            "باراسيتامول", "أموكسيسيلين", "إيبوبروفين", "ميتفورمين",
            "أتورفاستاتين", "أومبرازول", "سيفالكسين", "ديكلوفيناك",
            "سيليبريكس", "لوسارتان", "أملوديبين", "ميترونيدازول",
            "أزيثرومايسين", "دكساميثازون", "هيدروكورتيزون", "سالبيوتامول",
        }
        self.diagnoses = {
            "كسور", "التهاب", "مزمن", "حاد", "سكري", "ضغط", "ربو",
            "حساسية", "فقر دم", "هشاشة عظام", "التهاب المفاصل",
            "ضغط دم مرتفع", "التهاب رئوي", "التهاب الجيوب الأنفية",
        }
        logger.info("MedicalContext: using defaults (%d drugs, %d diagnoses)",
                    len(self.drugs), len(self.diagnoses))

    # ------------------------------------------------------------------
    def process_word(self, word: str, context_words: Optional[List[str]] = None) -> Dict:
        """Analyse a single word in a medical context.

        Priority order:
        1. Orthopedic terms (exact variant match)
        2. Orthopedic terms (fuzzy match)
        3. Dosage patterns
        4. Drug detection (fuzzy)
        5. Diagnosis detection (fuzzy)

        Returns dict with keys: type, corrected, normalized.
        """
        result: Dict = {"type": "general", "corrected": word, "normalized": word}

        if not word or not word.strip():
            return result

        # Normalise
        norm = self._normalize(word)
        result["normalized"] = norm

        # 1. Exact lookup in orthopedic term variants (highest priority)
        if norm in self.term_variants:
            correct_term = self.term_variants[norm]
            result["type"] = "orthopedic_term"
            result["corrected"] = correct_term
            result["confidence"] = 0.95
            return result

        # 2. Fuzzy match against orthopedic terms
        for correct_term, variants in self.orthopedic_terms.items():
            for variant in variants:
                norm_variant = self._normalize(variant)
                if (norm_variant in norm or norm in norm_variant or
                        SequenceMatcher(None, norm, norm_variant).ratio() > 0.7):
                    result["type"] = "orthopedic_term"
                    result["corrected"] = correct_term
                    result["confidence"] = 0.85
                    return result

        # 3. Detect dosage patterns
        if re.match(r'^\d+(\.\d+)?\s*(مجم|mg|مل|ml)?$', norm):
            result["type"] = "dosage"
            result["corrected"] = re.sub(r'(\d+(?:\.\d+)?)\s*(مجم|mg)', r'\1 مجم', norm)
            result["corrected"] = re.sub(r'(\d+(?:\.\d+)?)\s*(مل|ml)', r'\1 مل', result["corrected"])
            return result

        # 4. Detect drugs (fuzzy)
        for drug in self.drugs:
            if drug in norm or SequenceMatcher(None, norm, drug).ratio() > 0.75:
                result["type"] = "drug"
                result["corrected"] = drug
                return result

        # 5. Detect diagnoses (fuzzy)
        for diag in self.diagnoses:
            if diag in norm or SequenceMatcher(None, norm, diag).ratio() > 0.75:
                result["type"] = "diagnosis"
                result["corrected"] = diag
                return result

        return result

    def process_phrase(self, words: List[str]) -> List[Dict]:
        """معالجة عبارة كاملة للكشف عن مصطلحات متعددة الكلمات.

        Tries to combine 2-4 adjacent words into multi-word orthopedic terms.
        Returns a list of dicts with keys: text, corrected, type, start_idx, end_idx.
        """
        if not words:
            return []

        results: List[Dict] = []
        i = 0

        while i < len(words):
            found_term = False

            # محاولة دمج 2-4 كلمات للبحث عن مصطلح مركب
            for length in range(min(4, len(words) - i), 1, -1):
                phrase = ' '.join(words[i:i + length])
                norm_phrase = self._normalize(phrase)

                # البحث في قاموس المصطلحات
                if norm_phrase in self.term_variants:
                    correct_term = self.term_variants[norm_phrase]
                    results.append({
                        "text": phrase,
                        "corrected": correct_term,
                        "type": "orthopedic_phrase",
                        "start_idx": i,
                        "end_idx": i + length,
                    })
                    i += length
                    found_term = True
                    break

                # Fuzzy match for multi-word terms
                for correct_term, variants in self.orthopedic_terms.items():
                    for variant in variants:
                        norm_variant = self._normalize(variant)
                        if SequenceMatcher(None, norm_phrase, norm_variant).ratio() > 0.65:
                            results.append({
                                "text": phrase,
                                "corrected": correct_term,
                                "type": "orthopedic_phrase",
                                "start_idx": i,
                                "end_idx": i + length,
                            })
                            i += length
                            found_term = True
                            break
                    if found_term:
                        break
                if found_term:
                    break

            if not found_term:
                # معالجة كلمة واحدة
                single_result = self.process_word(words[i])
                results.append({
                    "text": words[i],
                    "corrected": single_result["corrected"] if single_result["type"] != "general" else words[i],
                    "type": single_result["type"],
                    "start_idx": i,
                    "end_idx": i + 1,
                })
                i += 1

        return results


# ==============================================================================
# 3. مصحح NLP عربي خفيف (CPU-Friendly)
# ==============================================================================
class NLPArabicCorrector:
    """Lightweight NLP corrector using SymSpell (optional, disabled by default).

    SymSpell is extremely fast on CPU (O(1) lookup).  It uses a pre-built
    dictionary of common Arabic medical words.  Enable via ``ENABLE_NLP_CORRECTION=1``.
    """

    def __init__(self, enabled: bool = False, model_cache_dir: str = "/data/nlp_models"):
        self.enabled = enabled
        self.cache_dir = Path(model_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, str] = {}
        self.sym = None

        if self.enabled:
            self._init_symspell()

    def _init_symspell(self):
        try:
            from symspellpy import SymSpell, Verbosity
            self.sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
            dict_path = self.cache_dir / "arabic_medical.dict"
            if not dict_path.exists():
                self._build_symspell_dict(dict_path)
            self.sym.load_dictionary(str(dict_path), term_index=0, count_index=1, separator=" ")
            logger.info("NLP SymSpell corrector initialized")
        except ImportError:
            logger.warning("symspellpy not installed — NLP correction disabled")
            self.enabled = False
        except Exception as e:
            logger.error("NLP corrector init failed: %s", e)
            self.enabled = False

    def _build_symspell_dict(self, path: Path):
        """Build a fast SymSpell dictionary from common Arabic medical words."""
        words = [
            # General medical
            "المحتويات", "جدول", "صفحة", "مريض", "جرعة", "علاج", "تشخيص",
            "كسر", "التهاب", "وصفة", "طبية", "دواء", "مستشفى", "طبيب",
            "تمريض", "مختبر", "أشعة", "تحليل", "دم", "بول", "قسطرة",
            # Drugs
            "باراسيتامول", "أموكسيسيلين", "إيبوبروفين", "ميتفورمين",
            "أتورفاستاتين", "أومبرازول", "سيفالكسين", "ديكلوفيناك",
            "لوسارتان", "أملوديبين", "ميترونيدازول", "أزيثرومايسين",
            "دكساميثازون", "هيدروكورتيزون", "سالبيوتامول", "سيليبريكس",
            # Anatomy / body parts
            "رأس", "صدر", "بطن", "ظهر", "رقبة", "ذراع", "ساق", "قدم",
            "عين", "أذن", "أنف", "فم", "حنجرة", "قلب", "رئة", "كبد",
            "كلية", "معدة", "أمعاء", "عظام", "مفاصل", "عضلات", "أعصاب",
            # Diagnoses
            "سكري", "ضغط", "ربو", "حساسية", "فقر دم", "هشاشة عظام",
            "التهاب المفاصل", "ضغط دم مرتفع", "التهاب رئوي",
            "التهاب الجيوب الأنفية", "التهاب المثانة", "حصوات الكلى",
            # Common words
            "مريض", "مريضة", "ذكر", "أنثى", "سنة", "أشهر", "أيام",
            "يوميا", "صباحا", "مساء", "قبل", "بعد", "الأكل", "النوم",
            "قرص", "كبسولة", "شراب", "حقنة", "كريم", "قطرة", "أمبول",
            "مجم", "مل", "مليغرام", "ملليتر",
        ]

        # Add orthopedic terms from the medical_terms_dict.json
        ortho_path = Path("/app/medical_terms_dict.json")
        if ortho_path.exists():
            try:
                import json as _json
                with open(ortho_path, 'r', encoding='utf-8') as f:
                    data = _json.load(f)
                for term in data.get("orthopedic_terms", {}):
                    words.append(term)
                    # Also add individual words from multi-word terms
                    for w in term.split():
                        if w and len(w) >= 3:
                            words.append(w)
                logger.info("Added orthopedic terms to SymSpell dict: total %d words", len(words))
            except Exception as e:
                logger.warning("Failed to add orthopedic terms to SymSpell: %s", e)

        with open(path, 'w', encoding='utf-8') as f:
            for w in words:
                f.write(f"{w} 1000\n")
        logger.info("Built SymSpell dictionary: %d words", len(words))

    def correct(self, text: str, min_confidence: float = 0.85) -> str:
        if not self.enabled or not self.sym or not text:
            return text

        words = text.split()
        corrected = []
        for word in words:
            if word.lower() in self._cache:
                corrected.append(self._cache[word])
                continue

            suggestions = self.sym.lookup(word, Verbosity.CLOSEST, max_edit_distance=2)
            if suggestions and suggestions[0].distance < 2:
                fixed = suggestions[0].term
                self._cache[word] = fixed
                corrected.append(fixed)
            else:
                corrected.append(word)

        return " ".join(corrected)


# ==============================================================================
# Pipeline — Orchestrator
# ==============================================================================
class AdvancedCorrectionPipeline:
    """Three-stage correction pipeline that runs after ensemble voting.

    Stages (in priority order):
    1. **User dictionary** — learned from manual corrections (highest priority)
    2. **Medical context** — drug/dosage/diagnosis detection + fuzzy matching
    3. **NLP correction** — SymSpell-based spell-check for low-confidence words
    """

    def __init__(
        self,
        enable_learning: bool = True,
        enable_medical_context: bool = True,
        enable_nlp: bool = False,
    ):
        self.learning: Optional[UserLearningEngine] = UserLearningEngine() if enable_learning else None
        self.medical: Optional[MedicalContextProcessor] = MedicalContextProcessor() if enable_medical_context else None
        self.nlp = NLPArabicCorrector(enabled=enable_nlp)
        self.enable_nlp = enable_nlp

    def process_word(self, word: str, confidence: float, context_words: Optional[List[str]] = None) -> Dict:
        """Process a single word through all pipeline stages.

        Returns dict: original, final, source, confidence.
        ``source`` is one of: ``none``, ``user_dictionary``, ``medical_drug``,
        ``medical_dosage``, ``medical_diagnosis``, ``nlp_sym_spell``.
        """
        result = {"original": word, "final": word, "source": "none", "confidence": confidence}

        # 1. User dictionary (highest priority)
        if self.learning:
            user_corr = self.learning.get_correction(word)
            if user_corr and user_corr != word:
                result["final"] = user_corr
                result["source"] = "user_dictionary"
                return result

        # 2. Medical context
        if self.medical:
            ctx_words = context_words or []
            med = self.medical.process_word(word, ctx_words)
            if med["type"] != "general" and med["corrected"] != word:
                result["final"] = med["corrected"]
                result["source"] = f"medical_{med['type']}"
                return result

        # 3. NLP correction (only for low-confidence words)
        if self.enable_nlp and confidence < 0.85:
            nlp_fixed = self.nlp.correct(word, min_confidence=confidence)
            if nlp_fixed != word:
                result["final"] = nlp_fixed
                result["source"] = "nlp_sym_spell"

        return result

    def learn_correction(self, wrong: str, correct: str, context: str = ""):
        """Record a user manual correction for future learning."""
        if self.learning:
            self.learning.learn(wrong, correct, context)


# ==============================================================================
# Module-level singleton
# ==============================================================================
_pipeline: Optional[AdvancedCorrectionPipeline] = None


def get_pipeline() -> AdvancedCorrectionPipeline:
    """Get or create the singleton pipeline (reads env vars each time)."""
    global _pipeline

    enable_learning = os.environ.get("ENABLE_USER_LEARNING", "1") == "1"
    enable_medical = os.environ.get("ENABLE_MEDICAL_CONTEXT", "1") == "1"
    enable_nlp = os.environ.get("ENABLE_NLP_CORRECTION", "0") == "1"

    if _pipeline is None:
        _pipeline = AdvancedCorrectionPipeline(
            enable_learning=enable_learning,
            enable_medical_context=enable_medical,
            enable_nlp=enable_nlp,
        )
    else:
        # Update toggle flags without re-initialising data
        _pipeline.learning = UserLearningEngine() if enable_learning else None
        if enable_medical and _pipeline.medical is None:
            _pipeline.medical = MedicalContextProcessor()
        elif not enable_medical:
            _pipeline.medical = None
        _pipeline.enable_nlp = enable_nlp
        if enable_nlp and not _pipeline.nlp.enabled:
            _pipeline.nlp = NLPArabicCorrector(enabled=True)

    return _pipeline