from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import upload, corrections, dictionaries, suggestions, deployment, reports, dicom
from app.database import engine, Base

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Medical Handwriting OCR",
    description="Adaptive OCR system for medical notes with human-in-the-loop correction, "
                "dictionary integration, DICOM support, and continuous learning",
    version="2.0.0"
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(upload.router)
app.include_router(corrections.router)
app.include_router(dictionaries.router)
app.include_router(suggestions.router)
app.include_router(deployment.router)
app.include_router(reports.router)
app.include_router(dicom.router)

# Health check
@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "2.0.0"}


@app.get("/")
async def root():
    return {
        "message": "Medical Handwriting OCR API v2.0",
        "version": "2.0.0",
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
            "docs": "/docs"
        }
    }
