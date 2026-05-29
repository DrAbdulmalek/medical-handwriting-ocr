from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql://ocr_user:ocr_password_123@localhost:5432/medical_ocr"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin123"
    MINIO_BUCKET: str = "ocr-crops"
    MINIO_SECURE: bool = False

    # OCR
    PADDLEOCR_LANG: str = "ar,en"  # Arabic + English
    TROCR_MODEL_PATH: str = None  # Will use default initially

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # Dictionary
    DICTIONARY_GITHUB_TOKEN: str = ""
    DICTIONARY_GITHUB_REPO: str = "medical-dictionaries"
    DICTIONARY_GITHUB_OWNER: str = "drabdulmalrk"
    DICTIONARY_DATA_DIR: str = "./data/dictionaries"

    # UMLS (optional)
    UMLS_API_KEY: str = ""
    UMLS_API_BASE: str = "https://uts.nlm.nih.gov"

    # Paths
    UPLOAD_DIR: str = "./uploads"
    CROP_DIR: str = "./crops"
    MODELS_DIR: str = "./models"
    REPLAY_BUFFER_PATH: str = "./replay_buffer.json"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings():
    return Settings()


settings = get_settings()
