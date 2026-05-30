"""
Application configuration for Medical Handwriting OCR.

All settings are loaded from environment variables with sensible defaults.
Use a `.env` file (or Docker env vars) to override defaults in production.
"""

from typing import List, Optional
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://ocr_user:ocr_password_123@localhost:5432/medical_ocr"

    # ── MinIO Object Storage ──────────────────────────────────
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin123"
    MINIO_BUCKET: str = "ocr-crops"
    MINIO_SECURE: bool = False

    # ── OCR Engine ─────────────────────────────────────────────
    PADDLEOCR_LANG: str = "ar,en"  # Arabic + English
    TROCR_MODEL_PATH: Optional[str] = None  # Uses default if None

    # ── Celery / Redis ──────────────────────────────────────────
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Dictionary ─────────────────────────────────────────────
    DICTIONARY_GITHUB_TOKEN: str = ""
    DICTIONARY_GITHUB_REPO: str = "medical-dictionaries"
    DICTIONARY_GITHUB_OWNER: str = "drabdulmalrk"
    DICTIONARY_DATA_DIR: str = "./data/dictionaries"

    # ── UMLS (optional) ──────────────────────────────────────
    UMLS_API_KEY: str = ""
    UMLS_API_BASE: str = "https://uts.nlm.nih.gov"

    # ── Security & Rate Limiting ──────────────────────────────
    API_KEY_ENABLED: bool = False  # Set to True in production
    ADMIN_TOKEN: str = ""  # Admin bypass token
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:8080,http://localhost:8000"
    DEFAULT_RATE_LIMIT: str = "100/minute"
    UPLOAD_RATE_LIMIT: str = "20/minute"
    CORRECTION_RATE_LIMIT: str = "60/minute"
    REDIS_RATE_LIMIT_URL: Optional[str] = None  # Falls back to REDIS_URL

    # ── Monitoring ─────────────────────────────────────────────
    ENVIRONMENT: str = "development"  # development | staging | production
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"  # json | text
    PROMETHEUS_ENABLED: bool = True

    # ── Paths ──────────────────────────────────────────────────
    UPLOAD_DIR: str = "./uploads"
    CROP_DIR: str = "./crops"
    MODELS_DIR: str = "./models"
    REPLAY_BUFFER_PATH: str = "./replay_buffer.json"

    # ── Workers ────────────────────────────────────────────────
    WORKERS: int = 4

    @property
    def allowed_origins_list(self) -> List[str]:
        """Parse comma-separated ALLOWED_ORIGINS into a list."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def redis_rate_limit_url(self) -> str:
        """Redis URL for rate limiting, with fallback."""
        return self.REDIS_RATE_LIMIT_URL or self.REDIS_URL

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings():
    return Settings()


settings = get_settings()
