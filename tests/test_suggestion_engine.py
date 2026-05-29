import pytest
from backend.app.suggestion_engine import SuggestionEngine, ArabicSoundex

class TestArabicSoundex:
    def test_encode_basic(self):
        code1 = ArabicSoundex.encode("كتاب")
        code2 = ArabicSoundex.encode("كتب")
        assert code1 == code2  # Same root

    def test_different_words(self):
        code1 = ArabicSoundex.encode("قلب")
        code2 = ArabicSoundex.encode("كلب")
        assert code1 != code2

class TestSuggestionEngine:
    def test_abbreviation_expansion(self):
        engine = SuggestionEngine()
        suggestions = engine._abbreviation_suggestions("ORIF")
        assert len(suggestions) > 0
        assert "Open Reduction Internal Fixation" in suggestions[0].text

    def test_historical_learning(self):
        engine = SuggestionEngine()
        engine.add_historical_correction("Ostecb", "Osteoblastoma")
        suggestions = engine._historical_suggestions("Ostecb")
        assert len(suggestions) > 0
        assert suggestions[0].text == "Osteoblastoma"
