"""
Document & Image Parsing Router
================================
Endpoints for multi-format document parsing (PDF, DOCX, PPTX, HTML),
table extraction, equation parsing, medical image analysis with Florence-2,
medical element detection, and batch processing.

All endpoints leverage the parsers module for production-quality extraction.
"""

import os
import uuid
import logging
import tempfile
from typing import Optional, List

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.parsers.document_parser import document_parser, DocumentParseResult
from app.parsers.table_extractor import table_extractor
from app.parsers.equation_parser import equation_parser
from app.parsers.image_processor import medical_image_processor
from app.parsers.medical_detector import medical_detector
from app.parsers.batch_processor import batch_processor

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/parse", tags=["parsers"])


# ─────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────

class BatchOptions(BaseModel):
    """Options for batch processing jobs."""
    ocr_engine: str = Field(default="paddleocr", description="OCR engine to use: paddleocr or trocr")
    extract_tables: bool = Field(default=True, description="Extract tables from documents")
    extract_images: bool = Field(default=True, description="Extract embedded images")
    language: str = Field(default="ar", description="Language: ar, en, or auto")
    priority: int = Field(default=5, ge=1, le=10, description="Processing priority (1=highest)")


class BatchCreateRequest(BaseModel):
    """Request body for creating a batch processing job."""
    file_names: List[str] = Field(..., description="List of file names to process")
    options: Optional[BatchOptions] = Field(default=None)

    class Config:
        json_schema_extra = {
            "example": {
                "file_names": ["prescription1.jpg", "lab_results2.png", "notes3.pdf"],
                "options": {
                    "ocr_engine": "paddleocr",
                    "extract_tables": True,
                    "extract_images": True,
                    "language": "ar"
                }
            }
        }


class ParseResponse(BaseModel):
    """Generic successful parse response."""
    success: bool = True
    message: str
    data: Optional[dict] = None


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@router.post(
    "/document",
    summary="Parse a document (PDF, DOCX, PPTX, HTML)",
    description=(
        "Upload a document file and extract structured content including "
        "text, images, tables, and metadata. Supports PDF, DOCX, PPTX, and HTML."
    ),
    responses={
        200: {"description": "Document parsed successfully"},
        400: {"description": "Unsupported file type or invalid file"},
        500: {"description": "Processing error"},
    },
)
async def parse_document(
    file: UploadFile = File(...),
    extract_tables: bool = Form(default=True),
    extract_images: bool = Form(default=True),
    user_id: str = Form(default="anonymous"),
    db: Session = Depends(get_db),
):
    """Parse an uploaded document into structured content."""
    allowed_extensions = {".pdf", ".docx", ".pptx", ".html", ".htm", ".txt"}
    file_ext = os.path.splitext(file.filename or "")[1].lower()

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{file_ext}'. Supported: {', '.join(allowed_extensions)}"
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    # Save to temp file for processing
    with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        result = document_parser.parse_document(
            file_path=tmp_path,
            file_type=file_ext.lstrip("."),
            extract_tables=extract_tables,
            extract_images=extract_images,
        )

        logger.info(
            "Document parsed: %s — %d pages, %d text blocks",
            file.filename, len(result.pages), sum(len(p.text_blocks) for p in result.pages)
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Document parsed successfully",
                "data": {
                    "file_name": file.filename,
                    "file_type": file_ext.lstrip("."),
                    "total_pages": len(result.pages),
                    "pages": [
                        {
                            "page_number": p.page_number,
                            "text_blocks": p.text_blocks,
                            "tables": [t.to_dict() for t in p.tables],
                            "images": [{"path": img.path, "description": img.description} for img in p.images],
                            "word_count": p.word_count,
                        }
                        for p in result.pages
                    ],
                    "metadata": result.metadata,
                },
            },
        )

    except Exception as e:
        logger.error("Document parsing failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Document parsing failed: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post(
    "/tables",
    summary="Extract tables from a document",
    description=(
        "Upload a document or image and extract all tables as structured data. "
        "Supports PDF and image files."
    ),
)
async def extract_tables(file: UploadFile = File(...)):
    """Extract tables from an uploaded file."""
    allowed_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
    file_ext = os.path.splitext(file.filename or "")[1].lower()

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type for table extraction: {file_ext}"
        )

    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        if file_ext == ".pdf":
            tables = table_extractor.extract_tables_from_pdf(tmp_path)
        else:
            tables = table_extractor.extract_tables_from_image(tmp_path)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Extracted {len(tables)} table(s)",
                "data": {
                    "file_name": file.filename,
                    "table_count": len(tables),
                    "tables": [t.to_dict() for t in tables],
                },
            },
        )

    except Exception as e:
        logger.error("Table extraction failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Table extraction failed: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post(
    "/equations",
    summary="Extract equations from an image",
    description="Upload an image and detect/parse mathematical equations to LaTeX.",
)
async def extract_equations(file: UploadFile = File(...)):
    """Detect and parse equations from an uploaded image."""
    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        equations = equation_parser.detect_equations(tmp_path)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Detected {len(equations)} equation(s)",
                "data": {
                    "equation_count": len(equations),
                    "equations": [
                        {
                            "bbox": eq.bbox,
                            "latex": eq.latex,
                            "confidence": eq.confidence,
                        }
                        for eq in equations
                    ],
                },
            },
        )

    except Exception as e:
        logger.error("Equation extraction failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Equation extraction failed: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post(
    "/image/analyze",
    summary="Analyze medical image with Florence-2",
    description=(
        "Upload a medical image for advanced analysis using Florence-2: "
        "captioning, object detection, OCR, and region classification."
    ),
)
async def analyze_medical_image(
    file: UploadFile = File(...),
    tasks: str = Form(
        default="caption,detect,ocr,classify",
        description="Comma-separated analysis tasks: caption, detect, ocr, classify"
    ),
):
    """Perform comprehensive medical image analysis."""
    contents = await file.read()
    task_list = [t.strip() for t in tasks.split(",")]

    valid_tasks = {"caption", "detect", "ocr", "classify"}
    invalid = set(task_list) - valid_tasks
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tasks: {invalid}. Valid: {valid_tasks}"
        )

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        result = medical_image_processor.process_medical_image(
            tmp_path,
            run_caption="caption" in task_list,
            run_detection="detect" in task_list,
            run_ocr="ocr" in task_list,
            run_classification="classify" in task_list,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Image analysis complete",
                "data": {
                    "caption": result.caption,
                    "objects": [obj.model_dump() for obj in result.detected_objects],
                    "ocr_text": result.ocr_text,
                    "regions": [r.model_dump() for r in result.region_classifications],
                    "confidence": result.overall_confidence,
                },
            },
        )

    except Exception as e:
        logger.error("Image analysis failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Image analysis failed: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post(
    "/medical/detect",
    summary="Detect medical elements in an image",
    description=(
        "Detect prescription blocks, drug names, dosage instructions, "
        "medical stamps, doctor signatures, and patient info areas."
    ),
)
async def detect_medical_elements(file: UploadFile = File(...)):
    """Detect medical-specific elements in an uploaded image."""
    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        result = medical_detector.detect_medical_elements(tmp_path)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Detected {len(result.detected_elements)} medical element(s)",
                "data": result.model_dump(),
            },
        )

    except Exception as e:
        logger.error("Medical element detection failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Detection failed: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post(
    "/batch",
    summary="Create a batch processing job",
    description=(
        "Submit multiple files for asynchronous batch processing. "
        "Returns a batch_id for tracking progress."
    ),
    status_code=202,
)
async def create_batch_job(
    request: BatchCreateRequest,
    user_id: str = Form(default="anonymous"),
):
    """Create a new batch processing job."""
    try:
        batch_id = batch_processor.create_batch(
            file_names=request.file_names,
            options=request.options.model_dump() if request.options else None,
        )

        # Start async processing
        batch_processor.process_batch_async(batch_id)

        logger.info("Batch job created: %s with %d files", batch_id, len(request.file_names))

        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "message": "Batch processing started",
                "data": {
                    "batch_id": str(batch_id),
                    "total_files": len(request.file_names),
                    "status": "processing",
                    "track_url": f"/api/parse/batch/{batch_id}/status",
                },
            },
        )

    except Exception as e:
        logger.error("Batch creation failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch creation failed: {str(e)}")


@router.get(
    "/batch/{batch_id}/status",
    summary="Get batch processing status",
    description="Check the status and progress of a batch processing job.",
)
async def get_batch_status(batch_id: str):
    """Retrieve the current status of a batch processing job."""
    try:
        status = batch_processor.get_batch_status(batch_id)

        if status is None:
            raise HTTPException(status_code=404, detail=f"Batch job '{batch_id}' not found")

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": status,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Batch status check failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")
