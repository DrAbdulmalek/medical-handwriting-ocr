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

    # Paths
    UPLOAD_DIR: str = "./uploads"
    CROP_DIR: str = "./crops"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings():
    return Settings()


settings = get_settings()
