#!/usr/bin/env python3
"""
Integration tests for the medical-ocr-postprocessor bridge and its
hook into the suggestion engine.

These tests use mocking to simulate the postprocessor library so they
run regardless of whether the actual ``medical_ocr_postprocessor`` pip
package is installed.
"""

import sys
import types
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ── Ensure the backend package is importable ────────────────────
# Since pytest.ini sets ``pythonpath = backend``, `app.*` modules are
# already on the path when running via ``pytest`` from the project root.

from app.suggestion_engine import Suggestion, SuggestionEngine


# ─────────────────────────────────────────────────────────────────
# 1. Bridge ImportError handling
# ─────────────────────────────────────────────────────────────────

class TestBridgeImportErrorHandling:
    """Verify that the bridge degrades gracefully when the library is absent."""

    def test_module_imports_without_postprocessor(self):
        """The bridge module should be importable even when
        medical_ocr_postprocessor is not installed."""
        import app.postprocessor_bridge as bridge_mod
        # The module-level flag should be False since we haven't mocked it
        assert hasattr(bridge_mod, "_POSTPROCESSOR_AVAILABLE")
        # It should also export the public API regardless
        assert hasattr(bridge_mod, "PostprocessorBridge")
        assert hasattr(bridge_mod, "get_postprocessor_bridge")

    def test_get_bridge_returns_none_when_unavailable(self):
        """When the library is not installed, get_postprocessor_bridge
        should return None without raising."""
        import app.postprocessor_bridge as bridge_mod

        # Force-reset the singleton so the test is deterministic
        bridge_mod._bridge_instance = None
        bridge_mod._POSTPROCESSOR_AVAILABLE = False

        result = bridge_mod.get_postprocessor_bridge()
        assert result is None

    def test_bridge_raises_import_error_when_forced(self):
        """Attempting to instantiate PostprocessorBridge when the library
        is missing should raise an ImportError."""
        import app.postprocessor_bridge as bridge_mod
        bridge_mod._POSTPROCESSOR_AVAILABLE = False

        with pytest.raises(ImportError, match="not installed"):
            bridge_mod.PostprocessorBridge()


# ─────────────────────────────────────────────────────────────────
# 2. Bridge functionality (with mocked postprocessor)
# ─────────────────────────────────────────────────────────────────

class TestBridgeWithMockedPostprocessor:
    """Test bridge methods using a mocked PostProcessor class."""

    @pytest.fixture(autouse=True)
    def _mock_postprocessor(self):
        """Inject a fake medical_ocr_postprocessor module into sys.modules."""
        fake_module = types.ModuleType("medical_ocr_postprocessor")

        fake_processor_cls = MagicMock()
        fake_instance = MagicMock()
        fake_instance.correct_word.return_value = "corrected_text"
        fake_instance.validate_medical_terms.return_value = {
            "is_valid": True,
            "phi_spans": [],
        }
        fake_processor_cls.return_value = fake_instance
        fake_module.PostProcessor = fake_processor_cls

        # Make it available before importing the bridge
        sys.modules["medical_ocr_postprocessor"] = fake_module

        # Re-import bridge to pick up the mocked module
        import importlib
        import app.postprocessor_bridge as bridge_mod
        importlib.reload(bridge_mod)

        yield fake_instance

        # Cleanup
        sys.modules.pop("medical_ocr_postprocessor", None)
        importlib.reload(bridge_mod)

    def test_correct_text(self):
        import app.postprocessor_bridge as bridge_mod
        bridge_mod._bridge_instance = None
        bridge = bridge_mod.get_postprocessor_bridge(force_new=True)
        assert bridge is not None

        result = bridge.correct_text("raw_ocr_text")
        assert result == "corrected_text"
        assert bridge.get_stats()["total_calls"] == 1

    def test_correct_empty_text(self):
        import app.postprocessor_bridge as bridge_mod
        bridge_mod._bridge_instance = None
        bridge = bridge_mod.get_postprocessor_bridge(force_new=True)

        assert bridge.correct_text("") == ""
        assert bridge.correct_text("   ") == "   "
        assert bridge.get_stats()["total_calls"] == 0

    def test_batch_correct(self):
        import app.postprocessor_bridge as bridge_mod
        bridge_mod._bridge_instance = None
        bridge = bridge_mod.get_postprocessor_bridge(force_new=True)

        results = bridge.batch_correct(["word1", "word2", "word3"])
        assert results == ["corrected_text"] * 3
        assert bridge.get_stats()["total_calls"] == 3

    def test_stats(self):
        import app.postprocessor_bridge as bridge_mod
        bridge_mod._bridge_instance = None
        bridge = bridge_mod.get_postprocessor_bridge(force_new=True)

        bridge.correct_text("test")
        stats = bridge.get_stats()
        assert stats["postprocessor_available"] is True
        assert stats["total_calls"] == 1
        assert "correction_rate" in stats

    def test_mask_phi_no_spans(self):
        import app.postprocessor_bridge as bridge_mod
        bridge_mod._bridge_instance = None
        bridge = bridge_mod.get_postprocessor_bridge(force_new=True)

        result = bridge.mask_phi("some text without phi")
        assert "some text" in result


# ─────────────────────────────────────────────────────────────────
# 3. Integration with suggestion engine
# ─────────────────────────────────────────────────────────────────

class TestSuggestionEnginePostprocessorIntegration:
    """Test that the suggestion engine properly merges postprocessor
    corrections."""

    def _make_bridge_mock(self, correction_map):
        """Create a mock PostprocessorBridge with predetermined corrections."""
        bridge = MagicMock()
        bridge.available = True

        def side_effect(text, **kwargs):
            corrected = correction_map.get(text, text)
            return [Suggestion(
                text=corrected,
                score=0.92,
                source="postprocessor",
                confidence="high",
                metadata={"original_text": text, "corrected_by": "medical-ocr-postprocessor"},
            )] if corrected != text else []

        bridge.correct_text = MagicMock(side_effect=side_effect)
        return bridge

    def test_engine_works_without_postprocessor(self):
        """Suggestion engine should function normally when no postprocessor
        is attached (backward compatibility)."""
        engine = SuggestionEngine()
        # _postprocessor_bridge should be None by default
        assert engine._postprocessor_bridge is None

        suggestions = engine.get_suggestions("ORIF", is_medical=True)
        # Should return abbreviation expansion without errors
        assert any(s.text == "Open Reduction Internal Fixation" for s in suggestions)

    def test_postprocessor_suggestions_merged(self):
        """When a postprocessor bridge is attached, its corrections
        should appear in the suggestion results."""
        engine = SuggestionEngine()

        # Mock a bridge that corrects "Ostecb" → "Osteoblastoma"
        bridge = self._make_bridge_mock({"Ostecb": "Osteoblastoma"})
        engine._postprocessor_bridge = bridge

        suggestions = engine.get_suggestions("Ostecb", is_medical=True)
        texts = [s.text for s in suggestions]

        # The postprocessor correction should be among suggestions
        assert "Osteoblastoma" in texts

        # Check that the source is tagged correctly
        pp_results = [s for s in suggestions if s.source == "postprocessor"]
        assert len(pp_results) > 0
        assert pp_results[0].confidence in ("high", "medium")

    def test_historical_high_frequency_overrides_postprocessor(self):
        """Historical corrections with frequency >= 8 should take
        priority over postprocessor corrections."""
        from app.postprocessor_integration import merge_suggestions

        existing = [
            Suggestion(
                text="Osteoblastoma",
                score=0.85,
                source="historical",
                confidence="high",
                metadata={"frequency": 10},
            )
        ]

        pp_suggestions = [
            Suggestion(
                text="Osteoblastoma",
                score=0.92,
                source="postprocessor",
                confidence="high",
                metadata={"original_text": "Ostecb"},
            )
        ]

        merged = merge_suggestions(existing, pp_suggestions)
        # The historical entry should win because frequency >= 8
        assert merged[0].source == "historical"
        assert merged[0].metadata["frequency"] == 10

    def test_postprocessor_wins_over_low_frequency_historical(self):
        """Postprocessor corrections should win over low-frequency
        historical corrections."""
        from app.postprocessor_integration import merge_suggestions

        existing = [
            Suggestion(
                text="Osteoblastoma",
                score=0.72,
                source="historical",
                confidence="medium",
                metadata={"frequency": 2},
            )
        ]

        pp_suggestions = [
            Suggestion(
                text="Osteoblastoma",
                score=0.92,
                source="postprocessor",
                confidence="high",
                metadata={"original_text": "Ostecb"},
            )
        ]

        merged = merge_suggestions(existing, pp_suggestions)
        assert merged[0].source == "postprocessor"
        assert merged[0].score == 0.92

    def test_deduplication_across_sources(self):
        """Same correction text from multiple sources should be
        deduplicated (highest score wins)."""
        from app.postprocessor_integration import merge_suggestions

        existing = [
            Suggestion(
                text="Aspirin",
                score=0.80,
                source="dictionary",
                confidence="medium",
                metadata={},
            ),
            Suggestion(
                text="Aspirin",
                score=0.70,
                source="edit_distance",
                confidence="low",
                metadata={},
            ),
        ]

        pp_suggestions = [
            Suggestion(
                text="Aspirin",
                score=0.92,
                source="postprocessor",
                confidence="high",
                metadata={"original_text": "Aspirm"},
            )
        ]

        merged = merge_suggestions(existing, pp_suggestions)
        aspirin_entries = [s for s in merged if s.text == "Aspirin"]
        assert len(aspirin_entries) == 1
        assert aspirin_entries[0].source == "postprocessor"

    def test_no_postprocessor_empty_results(self):
        """When postprocessor returns no corrections, existing
        suggestions should remain unchanged."""
        from app.postprocessor_integration import merge_suggestions

        existing = [
            Suggestion(
                text="Aspirin",
                score=0.80,
                source="dictionary",
                confidence="medium",
                metadata={},
            )
        ]

        merged = merge_suggestions(existing, [])
        assert len(merged) == 1
        assert merged[0].text == "Aspirin"

    def test_integrate_with_suggestions_attaches_bridge(self):
        """integrate_with_suggestions() should attach the bridge to the
        engine instance."""
        engine = SuggestionEngine()
        bridge = MagicMock()
        bridge.available = True

        # Import the integration function (may fail if postprocessor not available,
        # but in test context it's mocked above)
        try:
            from app.postprocessor_integration import integrate_with_suggestions
            integrate_with_suggestions(engine, bridge)
            assert engine._postprocessor_bridge is bridge
        except ImportError:
            pytest.skip("postprocessor_integration not importable in this environment")
