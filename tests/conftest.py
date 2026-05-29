"""
Test configuration and fixtures for pytest.
"""

import os
import pytest
import json
from unittest.mock import MagicMock, patch
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set test environment variables before importing app modules
os.environ.setdefault("DATABASE_URL", "postgresql://ocr_user:ocr_password_123@localhost:5432/medical_ocr_test")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minioadmin")
os.environ.setdefault("MINIO_SECRET_KEY", "minioadmin123")
os.environ.setdefault("MINIO_BUCKET", "test-ocr-crops")
os.environ.setdefault("MINIO_SECURE", "false")


# Use in-memory SQLite for testing
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def test_engine():
    """Create a test database engine."""
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    yield engine
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(test_engine):
    """Create a fresh database session for each test."""
    from app.database import Base

    tables = Base.metadata.tables.values()
    Base.metadata.create_all(test_engine)

    connection = test_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def sample_text_regions():
    """Sample text regions for testing."""
    return [
        {
            "id": "uuid-001",
            "bbox": {"x1": 100, "y1": 200, "x2": 250, "y2": 230},
            "predicted_text": "Ostecb(astoma",
            "confidence": 0.62,
            "corrected_text": "Osteoblastoma",
            "script_class": "latin",
            "is_medical_term": True,
            "status": "gold_standard",
        },
        {
            "id": "uuid-002",
            "bbox": {"x1": 300, "y1": 200, "x2": 400, "y2": 230},
            "predicted_text": "الفقرات القطنية",
            "confidence": 0.88,
            "corrected_text": None,
            "script_class": "arabic",
            "is_medical_term": False,
            "status": "approved",
        },
        {
            "id": "uuid-003",
            "bbox": {"x1": 100, "y1": 250, "x2": 280, "y2": 280},
            "predicted_text": "FOGMACIN",
            "confidence": 0.45,
            "corrected_text": "GENTAMICIN",
            "script_class": "latin",
            "is_medical_term": True,
            "status": "pending",
        },
    ]


@pytest.fixture
def sample_crop_image():
    """Create a small test image."""
    import numpy as np
    from PIL import Image
    img = Image.new("RGB", (100, 30), color=(255, 255, 255))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()


@pytest.fixture
def mock_storage():
    """Mock MinIO storage service."""
    storage = MagicMock()
    storage.upload_crop.return_value = "crops/test-uuid.png"
    storage.get_crop_url.return_value = "http://localhost:9000/ocr-crops/crops/test-uuid.png"
    return storage
