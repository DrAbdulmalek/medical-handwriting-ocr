#!/usr/bin/env python3
"""
Smart Suggestion Engine
Combines dictionary, edit distance, phonetic, historical, and context suggestions
"""

import os
import json
import random
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import defaultdict, Counter
from difflib import SequenceMatcher

import numpy as np
from rapidfuzz import fuzz, process

from app.dictionary_client import DictionaryManager, get_dictionary_manager

# ── Optional: postprocessor integration ──────────────────────────
try:
    from app.postprocessor_bridge import PostprocessorBridge, get_postprocessor_bridge
    from app.postprocessor_integration import (
        get_postprocessor_suggestions,
        merge_suggestions,
        integrate_with_suggestions,
    )
    _POSTPROCESSOR_INTEGRATION_AVAILABLE = True
except ImportError:
    _POSTPROCESSOR_INTEGRATION_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Suggestion:
    text: str
    score: float
    source: str
    confidence: str
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'text': self.text,
            'score': round(self.score, 3),
            'source': self.source,
            'confidence': self.confidence,
            'metadata': self.metadata
        }


class ArabicSoundex:
    """Phonetic matching for Arabic"""

    GROUPS = {
        '1': 'أإآا', '2': 'ب', '3': 'تث', '4': 'ج',
        '5': 'حخ', '6': 'دذ', '7': 'ر', '8': 'ز',
        '9': 'سش', '10': 'صض', '11': 'طظ', '12': 'عغ',
        '13': 'ف', '14': 'ق', '15': 'ك', '16': 'ل',
        '17': 'م', '18': 'ن', '19': 'هة', '20': 'وؤ',
        '21': 'يئى',
    }

    LETTER_TO_GROUP = {}
    for group, letters in GROUPS.items():
        for letter in letters:
            LETTER_TO_GROUP[letter] = group

    @classmethod
    def encode(cls, text: str) -> str:
        normalized = text.strip()
        normalized = ''.join(c for c in normalized if '\u0600' <= c <= '\u06FF')

        code = []
        prev_group = None
        for char in normalized:
            group = cls.LETTER_TO_GROUP.get(char)
            if group and group != prev_group:
                code.append(group)
                prev_group = group

        return '-'.join(code)


class SuggestionEngine:
    """Main suggestion engine"""

    def __init__(self, dictionary_manager: Optional[DictionaryManager] = None, max_suggestions: int = 5):
        self.dictionary_manager = dictionary_manager or get_dictionary_manager()
        self.max_suggestions = max_suggestions

        self.historical_db_path = Path("./data/historical_corrections.json")
        self.historical_corrections: Dict[str, List[Dict]] = defaultdict(list)
        self._load_historical()

        # Optional postprocessor bridge — attached via integrate_with_suggestions()
        self._postprocessor_bridge: Optional["PostprocessorBridge"] = None

        self.context_patterns = {
            'الفقارة': {'القطنية', 'الصدرية', 'العجزية', 'العنقية'},
            'Osteo': {'blastoma', 'sarcoma', 'myeloma', 'chondroma'},
            'Chondro': {'blastoma', 'sarcoma', 'ma'},
            'Fibrous': {'dysplasia', 'tissue', 'histiocytoma'},
        }

        self.medical_abbreviations = {
            'ORIF': 'Open Reduction Internal Fixation',
            'AVN': 'Avascular Necrosis',
            'CT': 'Computed Tomography',
            'MRI': 'Magnetic Resonance Imaging',
            'FX': 'Fracture',
            'HX': 'History',
            'TX': 'Treatment',
            'DX': 'Diagnosis',
            'RX': 'Prescription',
            'BX': 'Biopsy',
        }

    def _load_historical(self):
        if self.historical_db_path.exists():
            try:
                with open(self.historical_db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.historical_corrections = defaultdict(list, data)
            except Exception as e:
                logger.warning(f"Failed to load historical: {e}")

    def _save_historical(self):
        self.historical_db_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.historical_db_path, 'w', encoding='utf-8') as f:
            json.dump(dict(self.historical_corrections), f, ensure_ascii=False, indent=2)

    def add_historical_correction(self, original: str, corrected: str, context: Optional[str] = None):
        entry = {
            'corrected': corrected,
            'timestamp': datetime.now().isoformat(),
            'context': context,
            'frequency': 1
        }

        existing = [c for c in self.historical_corrections[original] if c['corrected'] == corrected]
        if existing:
            existing[0]['frequency'] += 1
        else:
            self.historical_corrections[original].append(entry)

        self._save_historical()

    def get_suggestions(self, text: str, context_before: Optional[str] = None,
                        context_after: Optional[str] = None,
                        script_class: Optional[str] = None,
                        is_medical: bool = False) -> List[Suggestion]:
        all_suggestions = []

        if self.dictionary_manager.enabled:
            all_suggestions.extend(self._dictionary_suggestions(text, is_medical))

        all_suggestions.extend(self._edit_distance_suggestions(text))

        if script_class in ('arabic', 'mixed'):
            all_suggestions.extend(self._phonetic_suggestions(text))

        all_suggestions.extend(self._historical_suggestions(text))

        if context_before or context_after:
            all_suggestions.extend(self._context_suggestions(text, context_before, context_after))

        if is_medical:
            all_suggestions.extend(self._abbreviation_suggestions(text))

        # ── Postprocessor corrections (optional) ────────────────────
        if _POSTPROCESSOR_INTEGRATION_AVAILABLE and self._postprocessor_bridge is not None:
            try:
                pp_suggestions = get_postprocessor_suggestions(
                    text, self._postprocessor_bridge, is_medical=is_medical
                )
                if pp_suggestions:
                    all_suggestions = merge_suggestions(all_suggestions, pp_suggestions)
            except Exception as exc:
                logger.debug(f"Postprocessor suggestions skipped for '{text}': {exc}")

        return self._rank_suggestions(all_suggestions, text)[:self.max_suggestions]

    def _dictionary_suggestions(self, text: str, is_medical: bool) -> List[Suggestion]:
        suggestions = []
        try:
            dictionaries = ['medical', 'anatomy'] if is_medical else None
            results = self.dictionary_manager.search_term(text, dictionaries)

            for entry in results[:3]:
                score = SequenceMatcher(None, text.lower(), entry.term.lower()).ratio()
                suggestions.append(Suggestion(
                    text=entry.term, score=score * 0.95,
                    source='dictionary',
                    confidence='high' if score > 0.8 else 'medium',
                    metadata={'dictionary': entry.dictionary, 'definition': entry.definition}
                ))
        except Exception as e:
            logger.debug(f"Dictionary search failed: {e}")
        return suggestions

    def _edit_distance_suggestions(self, text: str) -> List[Suggestion]:
        suggestions = []
        candidates = list(self.historical_corrections.keys())
        matches = process.extract(text, candidates, scorer=fuzz.ratio, limit=5)

        for match_text, score, _ in matches:
            if score > 60:
                corrections = self.historical_corrections[match_text]
                best = max(corrections, key=lambda x: x['frequency'])
                suggestions.append(Suggestion(
                    text=best['corrected'], score=score / 100.0 * 0.85,
                    source='edit_distance', confidence='medium' if score > 80 else 'low',
                    metadata={'original_match': match_text, 'frequency': best['frequency']}
                ))
        return suggestions

    def _phonetic_suggestions(self, text: str) -> List[Suggestion]:
        suggestions = []
        input_code = ArabicSoundex.encode(text)

        for original, corrections in self.historical_corrections.items():
            if any('\u0600' <= c <= '\u06FF' for c in original):
                if ArabicSoundex.encode(original) == input_code:
                    best = max(corrections, key=lambda x: x['frequency'])
                    suggestions.append(Suggestion(
                        text=best['corrected'], score=0.75,
                        source='phonetic', confidence='medium',
                        metadata={'soundex_code': input_code}
                    ))
        return suggestions

    def _historical_suggestions(self, text: str) -> List[Suggestion]:
        suggestions = []
        if text in self.historical_corrections:
            corrections = sorted(self.historical_corrections[text], key=lambda x: x['frequency'], reverse=True)
            for corr in corrections[:3]:
                freq_score = min(corr['frequency'] / 10.0, 1.0)
                suggestions.append(Suggestion(
                    text=corr['corrected'], score=freq_score * 0.9,
                    source='historical',
                    confidence='high' if corr['frequency'] > 5 else 'medium',
                    metadata={'frequency': corr['frequency']}
                ))
        return suggestions

    def _context_suggestions(self, text, context_before, context_after):
        suggestions = []
        context_words = []
        if context_before:
            context_words.extend(context_before.split()[-3:])
        if context_after:
            context_words.extend(context_after.split()[:3])

        for word in context_words:
            if word in self.context_patterns:
                for completion in self.context_patterns[word]:
                    if completion.lower().startswith(text.lower()[:3]):
                        suggestions.append(Suggestion(
                            text=completion, score=0.7,
                            source='context', confidence='medium',
                            metadata={'trigger_word': word}
                        ))
        return suggestions

    def _abbreviation_suggestions(self, text: str) -> List[Suggestion]:
        suggestions = []
        upper = text.upper()
        if upper in self.medical_abbreviations:
            suggestions.append(Suggestion(
                text=self.medical_abbreviations[upper], score=0.95,
                source='abbreviation', confidence='high',
                metadata={'abbreviation': text}
            ))
        return suggestions

    def _rank_suggestions(self, suggestions: List[Suggestion], original: str) -> List[Suggestion]:
        seen = {}
        for s in suggestions:
            key = s.text.lower()
            if key not in seen or seen[key].score < s.score:
                seen[key] = s

        ranked = sorted(seen.values(), key=lambda x: x.score, reverse=True)
        for s in ranked:
            if abs(len(s.text) - len(original)) <= 2:
                s.score = min(s.score * 1.05, 1.0)

        return sorted(ranked, key=lambda x: x.score, reverse=True)


from datetime import datetime

_suggestion_engine: Optional[SuggestionEngine] = None

def get_suggestion_engine() -> SuggestionEngine:
    global _suggestion_engine
    if _suggestion_engine is None:
        _suggestion_engine = SuggestionEngine()
    return _suggestion_engine
