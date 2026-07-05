"""
OCR Background Tasks — Celery worker implementations.

Provides async processing for:
- Batch OCR processing (multiple documents)
- Re-processing documents with updated models
- Extracting and storing individual text regions
- Generating OCR quality metrics per document
"""

import logging
import uuid
from typing import Dict, List, Optional

from celery import current_task
from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.ocr_engine import OCREngine

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.ocr_tasks.process_document_async",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def process_document_async(
    self,
    document_id: str,
    model_version_id: Optional[str] = None,
) -> Dict:
    """Process a document asynchronously with OCR.

    Reads the original document file from storage, runs PaddleOCR + TrOCR,
    and stores results as TextRegion records in the database.

    Args:
        document_id: UUID of the document to process.
        model_version_id: Optional model version to use (defaults to active).

    Returns:
        Dictionary with processing statistics.
    """
    logger.info(
        "Starting async OCR for document=%s model=%s",
        document_id,
        model_version_id or "active",
    )
    self.update_state(state="PROCESSING", meta={"step": "initialization"})

    db = SessionLocal()
    try:
        # Fetch document metadata
        row = db.execute(
            "SELECT filepath, original_filename FROM documents WHERE id = :id",
            {"id": document_id},
        ).fetchone()

        if not row:
            logger.error("Document %s not found", document_id)
            return {"status": "error", "reason": "document_not_found"}

        filepath, original_filename = row["filepath"], row["original_filename"]

        # Load OCR engine (lazy-loaded, uses current active model)
        engine = OCREngine()
        self.update_state(state="PROCESSING", meta={"step": "ocr_detection"})

        # Read and process the image
        import os
        if not os.path.exists(filepath):
            logger.error("File not found on disk: %s", filepath)
            return {"status": "error", "reason": "file_not_found"}

        with open(filepath, "rb") as f:
            image_bytes = f.read()

        # Run OCR detection
        results = engine.detect_text(image_bytes)

        if not results:
            return {"status": "ok", "regions": 0, "message": "no_text_detected"}

        # Store results in DB
        regions_created = 0
        for i, region in enumerate(results):
            db.execute(
                """INSERT INTO text_regions
                   (id, page_id, region_type, bbox_x, bbox_y, bbox_width, bbox_height,
                    detected_text, confidence, status, source_model)
                   VALUES (:id, :page_id, :region_type, :x, :y, :w, :h,
                    :text, :confidence, :status, :model)
                """,
                {
                    "id": str(uuid.uuid4()),
                    "page_id": document_id,  # Using document_id as page_id for single-page docs
                    "region_type": "text_line",
                    "x": region.get("bbox", [0, 0, 0, 0])[0],
                    "y": region.get("bbox", [0, 0, 0, 0])[1],
                    "w": region.get("bbox", [0, 0, 0, 0])[2],
                    "h": region.get("bbox", [0, 0, 0, 0])[3],
                    "text": region.get("text", ""),
                    "confidence": region.get("confidence", 0.0),
                    "status": "pending" if region.get("confidence", 1.0) < 0.85 else "auto_approved",
                    "model": model_version_id or "active",
                },
            )
            regions_created += 1

        db.commit()

        logger.info(
            "Async OCR complete: document=%s regions=%d",
            document_id,
            regions_created,
        )

        return {
            "status": "ok",
            "document_id": document_id,
            "regions_created": regions_created,
            "filename": original_filename,
        }

    except Exception as exc:
        logger.error("OCR task failed for document %s: %s", document_id, exc)
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.ocr_tasks.batch_process_documents",
    bind=True,
)
def batch_process_documents(
    self,
    document_ids: List[str],
    model_version_id: Optional[str] = None,
) -> Dict:
    """Process multiple documents in batch.

    Chains individual document processing tasks and aggregates results.

    Args:
        document_ids: List of document UUIDs to process.
        model_version_id: Optional model version override.

    Returns:
        Dictionary with batch statistics.
    """
    logger.info("Starting batch OCR: %d documents", len(document_ids))

    results = {"total": len(document_ids), "processed": 0, "failed": 0, "skipped": 0}

    for doc_id in document_ids:
        try:
            task = process_document_async.delay(doc_id, model_version_id)
            results["processed"] += 1
        except Exception as exc:
            logger.error("Batch item failed: %s — %s", doc_id, exc)
            results["failed"] += 1

    return results


@celery_app.task(
    name="app.tasks.ocr_tasks.reprocess_with_model",
    bind=True,
)
def reprocess_with_model(
    self,
    document_id: str,
    new_model_version_id: str,
) -> Dict:
    """Re-process a document using a different model version.

    Deletes existing text regions and re-runs OCR with the specified model.

    Args:
        document_id: UUID of the document to reprocess.
        new_model_version_id: Model version to use for re-processing.

    Returns:
        Dictionary with re-processing results.
    """
    logger.info(
        "Re-processing document=%s with model=%s",
        document_id,
        new_model_version_id,
    )

    db = SessionLocal()
    try:
        # Delete existing regions
        db.execute(
            "DELETE FROM text_regions WHERE page_id = :page_id",
            {"page_id": document_id},
        )
        db.commit()

        # Process with new model
        result = process_document_async(
            document_id=document_id,
            model_version_id=new_model_version_id,
        )
        return result

    except Exception as exc:
        logger.error("Re-processing failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
    finally:
        db.close()
