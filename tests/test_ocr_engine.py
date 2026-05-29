"""
Tests for the OCR Engine module.
"""

import pytest
import numpy as np
from PIL import Image
from unittest.mock import MagicMock, patch, PropertyMock
import io


class TestOCREngine:
    """Tests for the OCREngine class."""

    @pytest.fixture
    def engine(self):
        """Create an OCR engine with mocked PaddleOCR."""
        with patch('app.ocr_engine.PaddleOCR') as mock_paddle:
            mock_instance = MagicMock()
            mock_paddle.return_value = mock_instance
            from app.ocr_engine import ocr_engine
            return ocr_engine

    @pytest.mark.integration
    def test_classify_script_arabic(self, engine):
        """Test script classification for Arabic text."""
        result = engine.classify_script("الفقرات القطنية")
        assert result == "arabic"

    @pytest.mark.integration
    def test_classify_script_latin(self, engine):
        """Test script classification for Latin text."""
        result = engine.classify_script("Osteoblastoma")
        assert result == "latin"

    def test_classify_script_mixed(self, engine):
        """Test script classification for mixed text."""
        result = engine.classify_script("الـanterior")
        assert result == "mixed"

    def test_classify_script_numeric(self, engine):
        """Test script classification for numeric text."""
        result = engine.classify_script("123.45")
        assert result == "numeric"

    def test_classify_script_empty(self, engine):
        """Test script classification for empty text."""
        result = engine.classify_script("")
        assert result == "numeric"

    def test_crop_region_basic(self, engine):
        """Test basic crop extraction."""
        # Create a test image (100x100)
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[:, :] = [128, 128, 128]

        bbox = {"x1": 10, "y1": 20, "x2": 50, "y2": 60}
        crop_bytes = engine.crop_region(image, bbox)

        assert isinstance(crop_bytes, bytes)
        assert len(crop_bytes) > 0

    def test_crop_region_with_padding(self, engine):
        """Test crop extraction with padding."""
        image = np.zeros((100, 100, 3), dtype=np.uint8)

        bbox = {"x1": 5, "y1": 5, "x2": 20, "y2": 20}
        crop_bytes = engine.crop_region(image, bbox, padding=10)

        assert isinstance(crop_bytes, bytes)
        assert len(crop_bytes) > 0

    def test_crop_region_edge_clamping(self, engine):
        """Test that crop doesn't exceed image boundaries."""
        image = np.zeros((100, 100, 3), dtype=np.uint8)

        bbox = {"x1": 0, "y1": 0, "x2": 100, "y2": 100}
        crop_bytes = engine.crop_region(image, bbox, padding=50)

        assert isinstance(crop_bytes, bytes)

    def test_detect_regions_empty_result(self, engine):
        """Test handling of empty OCR results."""
        engine.paddle.ocr.return_value = None
        regions = engine.detect_regions("/tmp/test.png")
        assert regions == []

    def test_detect_regions_with_data(self, engine):
        """Test parsing OCR detection results."""
        engine.paddle.ocr.return_value = [[
            [
                [[100, 200], [250, 200], [250, 230], [100, 230]],  # bbox
                ("TestWord", 0.85),  # text and confidence
            ],
            [
                [[300, 200], [400, 200], [400, 230], [300, 230]],
                ("AnotherWord", 0.72),
            ],
        ]]

        regions = engine.detect_regions("/tmp/test.png")
        assert len(regions) == 2
        assert regions[0]["predicted_text"] == "TestWord"
        assert regions[0]["confidence"] == 0.85
        assert regions[0]["bbox"]["x1"] == 100
        assert regions[0]["bbox"]["x2"] == 250
        assert regions[0]["reading_order"] == 0


class TestScriptClassification:
    """Comprehensive tests for script classification."""

    @pytest.fixture
    def engine(self):
        with patch('app.ocr_engine.PaddleOCR'):
            from app.ocr_engine import OCREngine
            return OCREngine.__new__(OCREngine)

    @pytest.mark.parametrize("text,expected", [
        ("مرحبا", "arabic"),
        ("Hello", "latin"),
        ("مرحبا Hello", "mixed"),
        ("12345", "numeric"),
        ("الحالة L4-L5", "mixed"),
        ("Anterior", "latin"),
        ("الفقرة", "arabic"),
        ("3.5mg", "numeric"),
        ("", "numeric"),
    ])
    def test_classify_script_cases(self, engine, text, expected):
        """Test various script classification scenarios."""
        result = engine.classify_script(text)
        assert result == expected, f"Text '{text}' classified as '{result}', expected '{expected}'"
