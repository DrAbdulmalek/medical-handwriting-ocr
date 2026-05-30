"""
Medical Handwriting OCR — FastAPI Application

A production-ready OCR system for Arabic medical handwriting with:
- PaddleOCR + TrOCR dual-engine recognition
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
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI
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
    logger.info("Starting Medical Handwriting OCR API v3.0")
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
        "## Adaptive OCR System for Arabic Medical Notes\n\n"
        "This API provides end-to-end handwriting recognition for medical "
        "prescriptions and clinical notes, with support for:\n\n"
        "- **Dual-engine OCR**: PaddleOCR (fast) + TrOCR (accurate)\n"
        "- **Smart Corrections**: 6-strategy suggestion engine with dictionary, "
        "edit distance, Arabic Soundex, previous corrections, context, and "
        "medical abbreviation expansion\n"
        "- **Dictionary Integration**: Arabic medical term dictionaries with "
        "GitHub-based token authentication\n"
        "- **UMLS/SNOMED**: Medical term validation against the UMLS "
        "terminology server\n"
        "- **DICOM Support**: Upload and process DICOM medical images\n"
        "- **Async Processing**: Celery workers for background OCR tasks\n"
        "- **Continual Learning**: EWC-based fine-tuning with replay buffer\n"
        "- **PDF/Excel Reports**: Generate comprehensive OCR analytics reports\n\n"
        "### Authentication\n\n"
        "All endpoints accept an `X-API-Key` header for authentication. "
        "Admin endpoints require the `X-Admin-Token` header.\n\n"
        "### Rate Limiting\n\n"
        "- Default: 100 requests/minute per IP\n"
        "- Upload endpoint: 20 requests/minute per IP\n"
        "- Correction endpoint: 60 requests/minute per IP\n"
    ),
    version="3.0.0",
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
            "version": "3.0.0",
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
        "message": "Medical Handwriting OCR API v3.0",
        "version": "3.0.0",
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
            "docs": "/docs",
            "redoc": "/redoc",
            "metrics": "/metrics",
            "health": "/health",
        },
    }
