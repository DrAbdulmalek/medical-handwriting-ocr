import os
import uuid
import logging
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.storage import storage
from app.ocr_engine import ocr_engine
from app.models import OCRResult, RegionResponse
from app.validators.upload_validator import validate_upload, sanitize_filename
import cv2
import numpy as np

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload", response_model=OCRResult)
async def upload_document(
    file: UploadFile = File(...),
    user_id: str = "anonymous",
    db: Session = Depends(get_db)
):
    """
    Upload a document image, run OCR, store results.

    Validates file size, magic bytes, content type, and filename before processing.
    """
    # Read file contents
    contents = await file.read()

    # ── Upload validation (size, magic bytes, content-type, filename) ──
    is_valid, validation_error = validate_upload(contents, file.filename or "unnamed", file.content_type or "")
    if not is_valid:
        logger.warning("Upload validation failed: %s", validation_error)
        return JSONResponse(
            status_code=400,
            content={"error": "validation_error", "detail": validation_error},
        )

    # Sanitize filename for storage
    safe_filename = sanitize_filename(file.filename or "unnamed")
    image = cv2.imdecode(np.frombuffer(contents, np.uint8), cv2.IMREAD_COLOR)

    if image is None:
        raise HTTPException(400, "Could not decode image")

    # Save original temporarily
    temp_path = f"/tmp/{uuid.uuid4()}_{safe_filename}"
    cv2.imwrite(temp_path, image)

    try:
        # Run OCR detection
        regions_data = ocr_engine.detect_regions(temp_path)

        from sqlalchemy import text

        # Create document record
        doc_id = uuid.uuid4()
        db.execute(text("""
            INSERT INTO documents (id, file_name, original_path, user_id)
            VALUES (:id, :name, :path, :user)
        """), {
            "id": str(doc_id),
            "name": safe_filename,
            "path": temp_path,
            "user": user_id
        })

        # Create page record
        page_id = uuid.uuid4()
        h, w = image.shape[:2]
        db.execute(text("""
            INSERT INTO pages (id, document_id, page_number, image_path, width, height)
            VALUES (:id, :doc_id, 1, :path, :w, :h)
        """), {
            "id": str(page_id),
            "doc_id": str(doc_id),
            "path": temp_path,
            "w": w,
            "h": h
        })

        # Process each region
        region_responses = []
        needs_review = 0

        for region in regions_data:
            # Extract crop
            crop_bytes = ocr_engine.crop_region(image, region["bbox"])

            # Upload to MinIO
            crop_filename = f"{uuid.uuid4()}.png"
            object_name = storage.upload_crop(crop_bytes, crop_filename)

            # Classify script
            script = ocr_engine.classify_script(region["predicted_text"])

            # Determine if needs review
            needs_review_flag = region["confidence"] < 0.75
            if needs_review_flag:
                needs_review += 1

            # Insert region
            region_id = uuid.uuid4()
            db.execute(text("""
                INSERT INTO text_regions
                (id, page_id, bbox, script_class, predicted_text, confidence,
                 model_version, status, user_id)
                VALUES
                (:id, :page_id, :bbox, :script, :text, :conf, :model, :status, :user)
            """), {
                "id": str(region_id),
                "page_id": str(page_id),
                "bbox": str(region["bbox"]).replace("'", '"'),
                "script": script,
                "text": region["predicted_text"],
                "conf": region["confidence"],
                "model": "paddleocr-v1",
                "status": "pending" if needs_review_flag else "approved",
                "user": user_id
            })

            # Get crop URL
            crop_url = storage.get_crop_url(object_name)

            region_responses.append(RegionResponse(
                id=region_id,
                bbox=region["bbox"],
                predicted_text=region["predicted_text"],
                confidence=region["confidence"],
                status="pending" if needs_review_flag else "approved",
                crop_url=crop_url
            ))

        db.commit()

        return OCRResult(
            document_id=doc_id,
            page_id=page_id,
            regions=region_responses,
            total_regions=len(region_responses),
            needs_review=needs_review
        )

    finally:
        # Cleanup temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)
