"""
Medical Handwriting OCR — FastAPI Application

A comprehensive medical data analysis platform with:
- PaddleOCR + TrOCR dual-engine handwriting recognition
- Multi-format document parsing (PDF, DOCX, PPTX, HTML) via Marker + Surya
- Advanced medical image analysis via Florence-2
- Audio/Video transcription via Whisper + speaker diarization
- Medical web crawling (PubMed, NEJM, WHO guidelines)
- Batch processing engine (Celery)
- Dynamic text chunking & semantic splitting for RAG
- Structured medical data extraction (vitals, meds, diagnoses, labs)
- Patient profile builder with visit timeline
- FHIR R4 mapping for clinical interoperability
- LLM integration (LangChain) + RAG engine with vector search
- Clinical decision support (drug interactions, dosage validation, QA)
- Medical guideline tracking (WHO, CDC, AHA, ESC, NICE)
- Human-in-the-loop correction workflow
- Dictionary integration (Arabic medical terms)
- UMLS/SNOMED medical term validation
- Smart suggestion engine (6 strategies)
- DICOM file support
- Async task processing (Celery + Redis)
- Prometheus metrics & structured JSON logging
- API Key authentication & rate limiting
"""

import logging
import os
import uuid
import traceback
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.database import engine, Base
from app.routers import (
    upload,
    corrections,
    dictionaries,
    suggestions,
    deployment,
    reports,
    dicom,
    parsers,
    media,
    ai,
    clinical,
)

# ─────────────────────────────────────────────────────────────
# Logging Setup
# ─────────────────────────────────────────────────────────────
from app.middleware.logging_config import setup_logging, add_logging_middleware

setup_logging()
logger = logging.getLogger("medical_ocr")

# ─────────────────────────────────────────────────────────────
# Lifespan (startup / shutdown)
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables, warm models.  Shutdown: cleanup."""
    logger.info("Starting Medical Data Analysis Platform API v4.0")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified / created")
    yield
    logger.info("Shutting down Medical Handwriting OCR API")


# ─────────────────────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Medical Handwriting OCR API",
    description=(
        "## Comprehensive Medical Data Analysis Platform\n\n"
        "Evolved from an Arabic handwriting OCR system into a full-spectrum "
        "medical data analysis platform that processes **any medical data source**:\n\n"
        "### Core OCR (Original)\n"
        "- **Dual-engine OCR**: PaddleOCR (fast) + TrOCR (accurate) for handwriting\n"
        "- **Smart Corrections**: 6-strategy suggestion engine\n"
        "- **UMLS/SNOMED**: Medical term validation\n"
        "- **Continual Learning**: EWC-based fine-tuning with replay buffer\n\n"
        "### Document & Image Processing (OmniParse Integration)\n"
        "- **Document Parser**: PDF, DOCX, PPTX, HTML via Marker + Surya\n"
        "- **Table Extraction**: Advanced table recognition and structuring\n"
        "- **Medical Image Analysis**: Florence-2 captioning, detection, OCR\n"
        "- **Medical Element Detection**: Prescriptions, stamps, signatures\n\n"
        "### Media Processing\n"
        "- **Audio Transcription**: Whisper with Arabic/English support\n"
        "- **Video Processing**: Audio extraction, keyframe analysis, transcription\n"
        "- **Speaker Diarization**: Doctor/patient/nurse identification\n"
        "- **Web Crawling**: PubMed, NEJM, WHO guideline extraction\n\n"
        "### AI & Clinical Intelligence\n"
        "- **RAG Engine**: Vector search + LLM-powered medical QA\n"
        "- **Schema Extraction**: Vitals, medications, diagnoses, labs\n"
        "- **Patient Profiles**: Multi-visit timeline aggregation\n"
        "- **FHIR R4**: Standard clinical data interchange\n"
        "- **Clinical QA**: Evidence-based answers with citations\n"
        "- **Drug Interactions**: Safety checking with contraindication warnings\n"
        "- **Guideline Tracker**: Real-time monitoring of WHO, CDC, AHA updates\n\n"
        "### Infrastructure\n"
        "- **Batch Processing**: Celery-powered bulk processing\n"
        "- **20+ File Types**: PDF, DOC, PPT, PNG, MP4, MP3, WEB, DICOM\n"
        "- **MIT License**: Full commercial freedom\n\n"
        "### Authentication\n\n"
        "All endpoints accept an `X-API-Key` header. "
        "Admin endpoints require `X-Admin-Token`.\n\n"
        "### Rate Limiting\n\n"
        "- Default: 100 req/min | Upload: 20 req/min | Correction: 60 req/min\n"
    ),
    version="4.0.0",
    lifespan=lifespan,
    contact={
        "name": "Dr. Abdulmalek",
        "url": "https://github.com/DrAbdulmalek/medical-handwriting-ocr",
    },
    license_info={
        "name": "MIT",
        "url": "https://opensource.org/licenses/MIT",
    },
    openapi_tags=[
        {"name": "upload", "description": "Upload medical documents and images for OCR processing"},
        {"name": "corrections", "description": "Review and correct OCR results with human-in-the-loop"},
        {"name": "dictionaries", "description": "Manage Arabic medical term dictionaries"},
        {"name": "suggestions", "description": "Smart OCR correction suggestions using 6 strategies"},
        {"name": "deployment", "description": "Model version management and deployment"},
        {"name": "reports", "description": "Generate PDF/Excel analytics reports"},
        {"name": "dicom", "description": "Upload and process DICOM medical images"},
        {"name": "parsers", "description": "Multi-format document parsing, table extraction, medical image analysis, batch processing"},
        {"name": "media", "description": "Audio/video transcription, speaker diarization, web crawling, universal content extraction"},
        {"name": "ai", "description": "Text chunking, schema extraction, patient profiles, FHIR conversion, RAG engine"},
        {"name": "clinical", "description": "Guideline tracking, clinical QA, drug interactions, dosage validation, progress tracking"},
    ],
)

# ─────────────────────────────────────────────────────────────
# Middleware Stack (order matters: last added = first executed)
# ─────────────────────────────────────────────────────────────

# 1. CORS — configurable allowed origins from ALLOWED_ORIGINS env
from app.middleware.cors_config import setup_cors

setup_cors(app)

# 2. Structured JSON logging middleware (request IDs, timing)
add_logging_middleware(app)

# 3. Prometheus request metrics middleware
from app.metrics import MetricsMiddleware

app.add_middleware(MetricsMiddleware)

# 4. Rate limiting (SlowAPI with Redis backend)
from app.middleware.rate_limiter import register_rate_limiter

register_rate_limiter(app)

# 5. API Key authentication middleware
from app.middleware.api_key_auth import APIKeyMiddleware

app.add_middleware(APIKeyMiddleware)

# 6. Security headers (XSS, Clickjacking, HSTS, CSP)
from app.middleware.security_headers import setup_security_headers

setup_security_headers(app)

# ─────────────────────────────────────────────────────────────
# Global Exception Handlers
# ─────────────────────────────────────────────────────────────

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle request validation errors with structured response."""
    errors = []
    for error in exc.errors():
        errors.append({
            "field": ".".join(str(loc) for loc in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        })
    logger.warning(f"Validation error on {request.url}: {errors}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "validation_error",
            "detail": errors,
            "path": str(request.url),
        },
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with structured response."""
    logger.warning(f"HTTP {exc.status_code} on {request.url}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code,
            "path": str(request.url),
        },
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions with logging and structured response."""
    error_id = str(uuid.uuid4())[:8]
    logger.error(
        f"Unhandled error [{error_id}] on {request.url}: {str(exc)}\n{traceback.format_exc()}"
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_server_error",
            "error_id": error_id,
            "message": "An unexpected error occurred. Please try again or contact support.",
            "path": str(request.url),
        },
    )

# ─────────────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────────────

app.include_router(upload.router, tags=["upload"])

app.include_router(corrections.router, tags=["corrections"])
app.include_router(dictionaries.router, tags=["dictionaries"])
app.include_router(suggestions.router, tags=["suggestions"])
app.include_router(deployment.router, tags=["deployment"])
app.include_router(reports.router, tags=["reports"])
app.include_router(dicom.router, tags=["dicom"])

# ─────────────────────────────────────────────────────────────
# New Routers (OmniParse Integration)
# ─────────────────────────────────────────────────────────────

app.include_router(parsers.router, tags=["parsers"])
app.include_router(media.router, tags=["media"])
app.include_router(ai.router, tags=["ai"])
app.include_router(clinical.router, tags=["clinical"])

# ─────────────────────────────────────────────────────────────
# Prometheus Metrics Endpoint
# ─────────────────────────────────────────────────────────────

from starlette.routing import Mount
from app.metrics import _metrics_handler

app.add_route("/metrics", _metrics_handler, methods=["GET"])

# ─────────────────────────────────────────────────────────────
# Health Check & Root
# ─────────────────────────────────────────────────────────────

@app.get(
    "/health",
    tags=["system"],
    summary="Health check",
    description="Returns service health status, version, and component availability.",
    responses={
        200: {
            "description": "Service is healthy",
            "content": {
                "application/json": {
                    "example": {
                        "status": "healthy",
                        "version": "3.0.0",
                        "components": {
                            "database": "ok",
                            "redis": "ok",
                            "minio": "ok",
                        },
                    }
                }
            },
        }
    },
)
async def health_check() -> Dict:
    """Check the health of all dependent services."""
    components = {"database": "unknown", "redis": "unknown", "minio": "unknown"}

    # Check database
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        components["database"] = "ok"
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        components["database"] = f"error: {str(e)[:50]}"

    # Check Redis
    try:
        import redis as redis_lib
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = redis_lib.from_url(redis_url)
        r.ping()
        r.close()
        components["redis"] = "ok"
    except Exception:
        components["redis"] = "not configured"

    # Check MinIO
    try:
        from app.storage import get_minio_client
        client = get_minio_client()
        if client.bucket_exists("ocr-crops"):
            components["minio"] = "ok"
        else:
            components["minio"] = "bucket missing"
    except Exception:
        components["minio"] = "not configured"

    all_ok = all(v == "ok" for v in components.values())

    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "healthy" if all_ok else "degraded",
            "version": "4.0.0",
            "components": components,
        },
    )


@app.get(
    "/",
    tags=["system"],
    summary="API root",
    description="Returns API information and available endpoints.",
    responses={
        200: {
            "description": "API information",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Medical Handwriting OCR API v3.0",
                        "version": "3.0.0",
                        "docs": "/docs",
                        "metrics": "/metrics",
                    }
                }
            },
        }
    },
)
async def root() -> Dict:
    """API root — returns version and available endpoints."""
    return {
        "message": "Medical Data Analysis Platform API v4.0",
        "version": "4.0.0",
        "endpoints": {
            "upload": "POST /api/upload",
            "correct": "POST /api/correct",
            "pending": "GET /api/pending",
            "approve": "POST /api/approve/{region_id}",
            "dictionaries": "GET /api/dictionaries/",
            "suggestions": "GET /api/suggestions/",
            "deployment": "GET /api/deployment/status",
            "reports": "GET /api/reports/generate",
            "dicom": "POST /api/dicom/upload",
            "parse_document": "POST /api/parse/document",
            "parse_tables": "POST /api/parse/tables",
            "analyze_image": "POST /api/parse/image/analyze",
            "medical_detect": "POST /api/parse/medical/detect",
            "batch_process": "POST /api/parse/batch",
            "audio_transcribe": "POST /api/media/audio/transcribe",
            "video_transcribe": "POST /api/media/video/transcribe",
            "speaker_diarize": "POST /api/media/diarize",
            "web_crawl": "POST /api/media/web/crawl",
            "pubmed_search": "GET /api/media/web/pubmed",
            "universal_extract": "POST /api/media/extract",
            "chunk_text": "POST /api/ai/chunk",
            "schema_extract": "POST /api/ai/schema/extract",
            "patient_profile": "POST /api/ai/patient/profile",
            "fhir_convert": "POST /api/ai/fhir/convert",
            "rag_index": "POST /api/ai/rag/index",
            "rag_search": "POST /api/ai/rag/search",
            "rag_ask": "POST /api/ai/rag/ask",
            "guidelines": "GET /api/clinical/guidelines",
            "clinical_qa": "POST /api/clinical/qa/ask",
            "drug_interactions": "POST /api/clinical/drug/interactions",
            "dosage_validate": "POST /api/clinical/dosage/validate",
            "progress": "GET /api/clinical/progress/{session_id}",
            "docs": "/docs",
            "redoc": "/redoc",
            "metrics": "/metrics",
            "health": "/health",
        },
    }
