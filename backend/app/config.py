"""
Application configuration for Medical Data Analysis Platform.

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

    # ── Document Parsing (Marker + Surya) ───────────────────
    MARKER_MODEL_NAME: str = "marker_single"
    SURYA_LANG: str = "ar,en"  # Languages for Surya OCR
    PDF_EXTRACT_IMAGES: bool = True
    PDF_DPI: int = 200

    # ── Image Processing (Florence-2) ─────────────────────────
    FLORENCE2_MODEL: str = "microsoft/Florence-2-large"
    FLORENCE2_DEVICE: str = "cuda"  # cuda or cpu

    # ── Equation Parsing (Pix2Tex) ───────────────────────────
    PIX2TEX_MODEL: str = "Pix2Tex/LaTeX-OCR"
    EQUATION_CONFIDENCE_THRESHOLD: float = 0.6

    # ── Audio/Video Processing (Whisper) ──────────────────────
    WHISPER_MODEL_SIZE: str = "base"  # tiny, base, small, medium, large
    WHISPER_DEVICE: str = "cuda"
    WHISPER_COMPUTE_TYPE: str = "float16"
    FFMPEG_PATH: str = "ffmpeg"  # Path to ffmpeg binary

    # ── Speaker Diarization (Pyannote) ──────────────────────
    DIARIZATION_MODEL: str = "pyannote/speaker-diarization-3.1"
    HF_TOKEN: str = ""  # HuggingFace token for gated models

    # ── Web Crawling ──────────────────────────────────────────
    CRAWLER_USER_AGENT: str = "MedicalDataBot/1.0"
    CRAWLER_RATE_LIMIT: float = 1.0  # Seconds between requests
    CRAWLER_TIMEOUT: int = 30  # HTTP request timeout
    CRAWLER_CACHE_DIR: str = "./cache/web"
    PUBMED_API_KEY: str = ""  # Optional NCBI API key
    PUBMED_EMAIL: str = ""

    # ── RAG / LLM ──────────────────────────────────────────────
    RAG_VECTOR_DB: str = "chromadb"  # chromadb or faiss
    RAG_PERSIST_DIR: str = "./data/vectorstore"
    RAG_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    RAG_CHUNK_SIZE: int = 512
    RAG_CHUNK_OVERLAP: int = 50
    RAG_TOP_K: int = 5
    LLM_PROVIDER: str = "openai"  # openai, local, or langchain
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4"
    LOCAL_LLM_URL: str = "http://localhost:11434"  # Ollama

    # ── FHIR ────────────────────────────────────────────────────
    FHIR_VERSION: str = "R4"
    FHIR_DEFAULT_RESOURCE_ID: str = "medical-ocr"

    # ── Clinical Decision Support ────────────────────────────
    DRUG_DATABASE_PATH: str = "./data/drug_interactions.json"
    GUIDELINE_CHECK_INTERVAL: int = 3600  # Seconds between guideline checks
    GUIDELINE_SOURCES: str = "WHO,CDC,AHA,ESC,NICE,MOH"

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

    # ── JWT Authentication ────────────────────────────────────
    SECRET_KEY: str = "CHANGE-ME-in-production-use-a-long-random-string"  # Used for JWT signing
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Security & Rate Limiting ──────────────────────────────
    API_KEY_ENABLED: bool = False  # Set to True in production
    ADMIN_TOKEN: str = ""  # Admin bypass token
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:8080,http://localhost:8000"
    DEFAULT_RATE_LIMIT: str = "100/minute"
    UPLOAD_RATE_LIMIT: str = "20/minute"
    CORRECTION_RATE_LIMIT: str = "60/minute"
    REDIS_RATE_LIMIT_URL: Optional[str] = None  # Falls back to REDIS_URL

    # ── Virus Scanning ──────────────────────────────────────────
    VIRUS_SCANNER_ENABLED: bool = False  # Enable virus scanning for uploads
    CLAMAV_ENABLED: bool = False  # Use ClamAV daemon for local scanning
    CLAMAV_HOST: str = "localhost"
    CLAMAV_PORT: int = 3310
    CLAMAV_TIMEOUT: int = 30
    VIRUSTOTAL_ENABLED: bool = False  # Use VirusTotal cloud API
    VIRUSTOTAL_API_KEY: str = ""
    VIRUSTOTAL_API_URL: str = "https://www.virustotal.com/api/v3"
    VIRUSTOTAL_TIMEOUT: int = 60
    VIRUSTOTAL_DETECTION_THRESHOLD: int = 1  # Min engine detections to flag

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
    BATCH_OUTPUT_DIR: str = "./batch_output"
    TEMP_DIR: str = "./tmp"

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

    @property
    def guideline_sources_list(self) -> List[str]:
        """Parse comma-separated GUIDELINE_SOURCES into a list."""
        return [s.strip() for s in self.GUIDELINE_SOURCES.split(",") if s.strip()]

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings():
    return Settings()


settings = get_settings()
