"""
DICOM API router.
Provides endpoints for uploading and processing DICOM medical imaging files.
"""

import uuid
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

router = APIRouter(prefix="/api/dicom", tags=["dicom"])


@router.post("/upload")
async def upload_dicom(
    file: UploadFile = File(...),
    user_id: str = "anonymous",
    db: Session = Depends(get_db)
):
    """
    Upload a DICOM file, extract metadata and text.
    Returns extracted text for OCR processing.
    """
    try:
        from app.dicom.reader import DICOMReader
    except ImportError:
        raise HTTPException(503, "DICOM support not available. Install pydicom.")

    contents = await file.read()

    try:
        reader = DICOMReader()
        ds = reader.read_from_bytes(contents)
        if ds is None:
            raise HTTPException(400, "Could not read DICOM file")

        # Extract metadata
        metadata = reader.get_metadata_summary_from_dataset(ds) if hasattr(reader, 'get_metadata_summary_from_dataset') else {
            "modality": str(getattr(ds, "Modality", "Unknown")),
            "rows": getattr(ds, "Rows", None),
            "columns": getattr(ds, "Columns", None),
        }

        # Extract text
        extracted_texts = []
        for tag_info in [
            ("PatientName", (0x0010, 0x0010)),
            ("StudyDescription", (0x0008, 0x1030)),
            ("SeriesDescription", (0x0008, 0x103E)),
            ("PatientComments", (0x0010, 0x4000)),
        ]:
            try:
                if tag_info[1] in ds:
                    value = str(ds[tag_info[1]].value).strip()
                    if value:
                        extracted_texts.append({"field": tag_info[0], "text": value})
            except Exception:
                continue

        # Get image for OCR
        image_bytes = None
        pixel_image = reader._get_pixel_image(ds)
        if pixel_image is not None:
            import io
            buffer = io.BytesIO()
            pixel_image.save(buffer, format='PNG')
            image_bytes = buffer.getvalue()

        return {
            "success": True,
            "metadata": metadata,
            "extracted_texts": extracted_texts,
            "has_pixel_data": image_bytes is not None,
        }

    except Exception as e:
        raise HTTPException(500, f"DICOM processing error: {str(e)}")


@router.get("/image/{document_id}")
async def get_dicom_image(document_id: str, db: Session = Depends(get_db)):
    """Get the DICOM pixel data as a PNG image for OCR processing."""
    try:
        from app.dicom.reader import DICOMReader
    except ImportError:
        raise HTTPException(503, "DICOM support not available")

    result = db.execute(text("""
        SELECT original_path FROM documents WHERE id = :id
    """), {"id": document_id}).fetchone()

    if not result:
        raise HTTPException(404, "Document not found")

    reader = DICOMReader()
    image_bytes = reader.get_image_for_ocr(result.original_path)

    if not image_bytes:
        raise HTTPException(404, "No pixel data available")

    return Response(content=image_bytes, media_type="image/png")
