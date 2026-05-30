"""
Batch Processor Module for Medical Handwriting OCR.

Provides Celery-based batch processing for entire patient folders,
hospital archives, or bulk document ingestion.  Tasks are distributed
across Celery workers for parallel processing with progress tracking
and comprehensive status reporting.

Uses the existing Celery app from ``app.celery_app``.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models
# =============================================================================


class BatchOptions(BaseModel):
    """Configuration options for a batch processing job."""

    extract_tables: bool = Field(True, description="Extract tables from documents")
    extract_images: bool = Field(True, description="Extract images from documents")
    detect_equations: bool = Field(False, description="Run equation detection")
    medical_detection: bool = Field(True, description="Run medical-specific detection")
    ocr_enabled: bool = Field(True, description="Run OCR on all pages")
    language: str = Field("ar,en", description="OCR languages (comma-separated)")
    max_file_size_mb: int = Field(100, description="Maximum file size in MB")
    supported_extensions: List[str] = Field(
        default_factory=lambda: [
            "pdf", "png", "jpg", "jpeg", "tiff", "tif",
            "docx", "pptx", "html", "htm",
        ],
        description="File extensions to process",
    )


class BatchJob(BaseModel):
    """Represents a batch processing job."""

    batch_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_paths: List[str] = Field(default_factory=list)
    options: BatchOptions = Field(default_factory=BatchOptions)
    total_files: int = Field(0)
    status: str = Field("created", description="created | queued | processing | completed | failed | cancelled")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    started_at: Optional[str] = Field(None)
    completed_at: Optional[str] = Field(None)
    error_message: Optional[str] = Field(None)


class BatchResult(BaseModel):
    """Result of processing a single file in a batch."""

    file_path: str = Field("")
    file_name: str = Field("")
    file_type: str = Field("")
    success: bool = Field(False)
    processing_time_ms: float = Field(0.0)
    pages_processed: int = Field(0)
    text_regions_found: int = Field(0)
    tables_extracted: int = Field(0)
    images_extracted: int = Field(0)
    equations_found: int = Field(0)
    medical_elements: int = Field(0)
    has_arabic: bool = Field(False)
    error: Optional[str] = Field(None)


class BatchStatus(BaseModel):
    """Status of a batch processing job."""

    batch_id: str = Field("")
    status: str = Field("unknown")
    total_files: int = Field(0)
    completed_files: int = Field(0)
    failed_files: int = Field(0)
    progress_percent: float = Field(0.0)
    created_at: Optional[str] = Field(None)
    started_at: Optional[str] = Field(None)
    completed_at: Optional[str] = Field(None)
    elapsed_seconds: float = Field(0.0)
    estimated_remaining_seconds: float = Field(0.0)
    error_message: Optional[str] = Field(None)


class PatientBatchResult(BaseModel):
    """Result of processing an entire patient folder."""

    folder_path: str = Field("")
    patient_id: Optional[str] = Field(None, description="Extracted or assigned patient ID")
    total_files: int = Field(0)
    processed_files: int = Field(0)
    failed_files: int = Field(0)
    total_pages: int = Field(0)
    total_text_regions: int = Field(0)
    total_tables: int = Field(0)
    total_images: int = Field(0)
    processing_time_ms: float = Field(0.0)
    file_results: List[BatchResult] = Field(default_factory=list)
    has_arabic_content: bool = Field(False)
    errors: List[str] = Field(default_factory=list)


# =============================================================================
# In-memory batch state store (production would use Redis/DB)
# =============================================================================

_batch_store: Dict[str, Dict[str, Any]] = {}


def _get_batch_state(batch_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve batch state from the in-memory store."""
    return _batch_store.get(batch_id)


def _set_batch_state(batch_id: str, state: Dict[str, Any]) -> None:
    """Persist batch state to the in-memory store."""
    _batch_store[batch_id] = state


# =============================================================================
# BatchProcessor
# =============================================================================


class BatchProcessor:
    """
    Batch processing engine for medical documents.

    Orchestrates processing of multiple files using Celery for
    distributed task execution.  Supports:

    * Processing individual file lists
    * Walking entire patient folders recursively
    * Progress tracking with status polling
    * Configurable processing options per batch
    * Result aggregation and reporting
    """

    def __init__(self) -> None:
        self._output_dir: str = str(settings.UPLOAD_DIR)
        os.makedirs(self._output_dir, exist_ok=True)
        logger.info("BatchProcessor initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_batch(
        self,
        file_paths: List[str],
        options: Optional[BatchOptions] = None,
    ) -> str:
        """
        Create a new batch job and queue it for processing.

        Parameters
        ----------
        file_paths : list[str]
            List of file paths to process.
        options : BatchOptions, optional
            Processing options.  Uses defaults if ``None``.

        Returns
        -------
        str
            The batch ID for tracking progress.
        """
        opts = options or BatchOptions()

        # Validate and filter files
        valid_paths = self._validate_files(file_paths, opts)

        batch_id = str(uuid.uuid4())
        job = BatchJob(
            batch_id=batch_id,
            file_paths=valid_paths,
            options=opts,
            total_files=len(valid_paths),
        )

        # Store initial state
        _set_batch_state(batch_id, {
            "job": job.model_dump(),
            "results": [],
            "completed_count": 0,
            "failed_count": 0,
        })

        logger.info(
            "Batch created: %s with %d files (valid), %d total input",
            batch_id,
            len(valid_paths),
            len(file_paths),
        )

        return batch_id

    def process_batch_async(self, batch_id: str) -> str:
        """
        Submit a batch for async processing via Celery.

        Parameters
        ----------
        batch_id : str
            ID of a previously created batch.

        Returns
        -------
        str
            The Celery task ID for tracking.
        """
        state = _get_batch_state(batch_id)
        if state is None:
            raise ValueError(f"Batch {batch_id} not found")

        job = BatchJob(**state["job"])
        job.status = "queued"
        state["job"] = job.model_dump()
        _set_batch_state(batch_id, state)

        try:
            from app.celery_app import celery_app

            # Dispatch the batch task
            result = process_batch_task.delay(batch_id)
            logger.info(
                "Batch %s queued as Celery task %s",
                batch_id,
                result.id,
            )
            return result.id

        except Exception as exc:
            logger.error("Failed to queue batch %s: %s", batch_id, exc)
            job.status = "failed"
            job.error_message = str(exc)
            state["job"] = job.model_dump()
            _set_batch_state(batch_id, state)
            raise

    def get_batch_status(self, batch_id: str) -> BatchStatus:
        """
        Get the current status of a batch job.

        Parameters
        ----------
        batch_id : str
            The batch ID to query.

        Returns
        -------
        BatchStatus
            Current status with progress information.
        """
        state = _get_batch_state(batch_id)
        if state is None:
            return BatchStatus(batch_id=batch_id, status="not_found")

        job = BatchJob(**state["job"])
        completed = state.get("completed_count", 0)
        failed = state.get("failed_count", 0)
        total = job.total_files

        now = datetime.now(timezone.utc)

        # Calculate progress
        progress = 0.0
        if total > 0:
            progress = ((completed + failed) / total) * 100.0

        # Calculate elapsed time
        elapsed = 0.0
        if job.started_at:
            started = datetime.fromisoformat(job.started_at)
            elapsed = (now - started).total_seconds()

        # Estimate remaining time
        remaining = 0.0
        if completed > 0 and (completed + failed) < total:
            avg_time_per_file = elapsed / max(completed, 1)
            remaining = avg_time_per_file * (total - completed - failed)

        return BatchStatus(
            batch_id=batch_id,
            status=job.status,
            total_files=total,
            completed_files=completed,
            failed_files=failed,
            progress_percent=round(progress, 1),
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            elapsed_seconds=round(elapsed, 1),
            estimated_remaining_seconds=round(remaining, 1),
            error_message=job.error_message,
        )

    def process_patient_folder(
        self,
        folder_path: str,
        options: Optional[BatchOptions] = None,
    ) -> PatientBatchResult:
        """
        Process all supported files in a patient folder recursively.

        This is a synchronous convenience method that processes all files
        in the given folder (and subfolders) in a single call.

        Parameters
        ----------
        folder_path : str
            Path to the patient folder.
        options : BatchOptions, optional
            Processing options.

        Returns
        -------
        PatientBatchResult
            Aggregated result for all files in the folder.
        """
        import time

        start = time.perf_counter()
        opts = options or BatchOptions()

        logger.info("Processing patient folder: %s", folder_path)

        if not os.path.isdir(folder_path):
            logger.error("Folder not found: %s", folder_path)
            return PatientBatchResult(
                folder_path=folder_path,
                errors=[f"Folder not found: {folder_path}"],
            )

        # Walk directory and collect files
        file_paths: List[str] = []
        for root, _dirs, files in os.walk(folder_path):
            for filename in files:
                ext = Path(filename).suffix.lstrip(".").lower()
                if ext in opts.supported_extensions:
                    file_paths.append(os.path.join(root, filename))

        if not file_paths:
            logger.warning("No supported files found in: %s", folder_path)
            return PatientBatchResult(
                folder_path=folder_path,
                errors=["No supported files found"],
            )

        logger.info("Found %d files to process in %s", len(file_paths), folder_path)

        # Create and process batch synchronously
        batch_id = self.create_batch(file_paths, opts)
        results = self._process_batch_sync(batch_id)

        # Aggregate results
        total_pages = sum(r.pages_processed for r in results)
        total_regions = sum(r.text_regions_found for r in results)
        total_tables = sum(r.tables_extracted for r in results)
        total_images = sum(r.images_extracted for r in results)
        processed = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        has_arabic = any(r.has_arabic for r in results)
        errors = [r.error for r in results if r.error]

        elapsed = (time.perf_counter() - start) * 1000

        # Try to extract patient ID from folder name
        folder_name = os.path.basename(folder_path.rstrip("/"))
        patient_id = self._extract_patient_id(folder_name, results)

        result = PatientBatchResult(
            folder_path=folder_path,
            patient_id=patient_id,
            total_files=len(file_paths),
            processed_files=processed,
            failed_files=failed,
            total_pages=total_pages,
            total_text_regions=total_regions,
            total_tables=total_tables,
            total_images=total_images,
            processing_time_ms=elapsed,
            file_results=results,
            has_arabic_content=has_arabic,
            errors=errors,
        )

        logger.info(
            "Patient folder processed: %d/%d files, %d pages, %.1fms",
            processed,
            len(file_paths),
            total_pages,
            elapsed,
        )
        return result

    # ------------------------------------------------------------------
    # Internal processing
    # ------------------------------------------------------------------

    def _process_batch_sync(self, batch_id: str) -> List[BatchResult]:
        """
        Process all files in a batch synchronously (blocking).

        This is used by ``process_patient_folder`` for synchronous execution.
        """
        state = _get_batch_state(batch_id)
        if state is None:
            return []

        job = BatchJob(**state["job"])
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc).isoformat()
        state["job"] = job.model_dump()

        results: List[BatchResult] = []
        completed = 0
        failed = 0

        for file_path in job.file_paths:
            try:
                result = self._process_single_file(file_path, job.options)
                results.append(result)
                if result.success:
                    completed += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.error("Failed to process %s: %s", file_path, exc)
                failed += 1
                results.append(
                    BatchResult(
                        file_path=file_path,
                        file_name=os.path.basename(file_path),
                        success=False,
                        error=str(exc),
                    )
                )

        # Update final state
        job.status = "completed" if failed == 0 else "completed_with_errors"
        job.completed_at = datetime.now(timezone.utc).isoformat()
        state["job"] = job.model_dump()
        state["results"] = [r.model_dump() for r in results]
        state["completed_count"] = completed
        state["failed_count"] = failed
        _set_batch_state(batch_id, state)

        return results

    def _process_single_file(
        self, file_path: str, options: BatchOptions
    ) -> BatchResult:
        """Process a single file and return its result."""
        import time

        start = time.perf_counter()
        file_name = os.path.basename(file_path)
        file_type = Path(file_path).suffix.lstrip(".").lower()

        result = BatchResult(
            file_path=file_path,
            file_name=file_name,
            file_type=file_type,
        )

        try:
            # Determine processing strategy based on file type
            if file_type == "pdf":
                result = self._process_pdf(file_path, options, result)
            elif file_type in ("png", "jpg", "jpeg", "tiff", "tif"):
                result = self._process_image(file_path, options, result)
            elif file_type in ("docx", "pptx", "html", "htm"):
                result = self._process_document(file_path, options, result)
            else:
                result.error = f"Unsupported file type: {file_type}"

            result.success = result.error is None

        except Exception as exc:
            logger.error("Error processing %s: %s", file_path, exc, exc_info=True)
            result.error = str(exc)
            result.success = False

        result.processing_time_ms = (time.perf_counter() - start) * 1000
        return result

    def _process_pdf(
        self, file_path: str, options: BatchOptions, result: BatchResult
    ) -> BatchResult:
        """Process a PDF file."""
        try:
            from app.parsers.document_parser import document_parser

            parse_result = document_parser.parse_document(file_path)
            result.pages_processed = parse_result.page_count
            result.has_arabic = parse_result.has_arabic

            if options.extract_tables:
                from app.parsers.table_extractor import table_extractor
                tables = table_extractor.extract_tables_from_pdf(file_path)
                result.tables_extracted = len(tables)

            if options.extract_images:
                images = document_parser.extract_images(file_path)
                result.images_extracted = len(images)

        except Exception as exc:
            result.error = f"PDF processing error: {exc}"

        return result

    def _process_image(
        self, file_path: str, options: BatchOptions, result: BatchResult
    ) -> BatchResult:
        """Process an image file."""
        try:
            result.pages_processed = 1

            if options.ocr_enabled:
                try:
                    from app.ocr_engine import ocr_engine

                    regions = ocr_engine.detect_regions(file_path)
                    result.text_regions_found = len(regions)

                    # Check for Arabic
                    for region in regions:
                        text = region.get("predicted_text", "")
                        if any("\u0600" <= c <= "\u06FF" for c in text):
                            result.has_arabic = True
                            break
                except Exception as ocr_exc:
                    logger.warning("OCR failed for %s: %s", file_path, ocr_exc)

            if options.detect_equations:
                try:
                    from app.parsers.equation_parser import equation_parser

                    equations = equation_parser.detect_equations(file_path)
                    result.equations_found = len(equations)
                except Exception as eq_exc:
                    logger.warning("Equation detection failed for %s: %s", file_path, eq_exc)

            if options.medical_detection:
                try:
                    from app.parsers.medical_detector import medical_object_detector

                    elements = medical_object_detector.detect_medical_elements(file_path)
                    result.medical_elements = (
                        len(elements.signatures)
                        + len(elements.stamps)
                        + len(elements.drug_names)
                        + len(elements.prescription_headers)
                    )
                except Exception as md_exc:
                    logger.warning("Medical detection failed for %s: %s", file_path, md_exc)

        except Exception as exc:
            result.error = f"Image processing error: {exc}"

        return result

    def _process_document(
        self, file_path: str, options: BatchOptions, result: BatchResult
    ) -> BatchResult:
        """Process a non-PDF document (DOCX, PPTX, HTML)."""
        try:
            from app.parsers.document_parser import document_parser

            parse_result = document_parser.parse_document(file_path)
            result.pages_processed = parse_result.page_count
            result.has_arabic = parse_result.has_arabic

            if options.extract_tables:
                from app.parsers.table_extractor import table_extractor

                tables = document_parser.extract_tables(file_path)
                result.tables_extracted = len(tables)

            if options.extract_images:
                images = document_parser.extract_images(file_path)
                result.images_extracted = len(images)

        except Exception as exc:
            result.error = f"Document processing error: {exc}"

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_files(file_paths: List[str], options: BatchOptions) -> List[str]:
        """Validate file paths and filter by supported extensions and size."""
        valid: List[str] = []
        max_bytes = options.max_file_size_mb * 1024 * 1024

        for path in file_paths:
            if not os.path.isfile(path):
                logger.warning("File not found, skipping: %s", path)
                continue

            ext = Path(path).suffix.lstrip(".").lower()
            if ext not in options.supported_extensions:
                logger.warning("Unsupported extension, skipping: %s", path)
                continue

            file_size = os.path.getsize(path)
            if file_size > max_bytes:
                logger.warning(
                    "File too large (%d MB > %d MB), skipping: %s",
                    file_size // (1024 * 1024),
                    options.max_file_size_mb,
                    path,
                )
                continue

            valid.append(path)

        return valid

    @staticmethod
    def _extract_patient_id(
        folder_name: str, results: List[BatchResult]
    ) -> Optional[str]:
        """
        Try to extract a patient ID from the folder name or file contents.

        Common patterns:
        * Folder named with patient ID: ``patient_12345``
        * Folder named with file number: ``FN-2024-001``
        * Folder named with national ID: ``1234567890``
        """
        import re

        # Try common ID patterns in folder name
        patterns = [
            r"(?:patient|pid|id)[-_](\d+)",
            r"(?:file[_-]?number|fn)[-_](\w+)",
            r"(?:national[_-]?id|nid)[-_](\d+)",
            r"(\d{7,})",  # Any 7+ digit number
        ]

        for pattern in patterns:
            match = re.search(pattern, folder_name, re.IGNORECASE)
            if match:
                return match.group(1)

        return None


# =============================================================================
# Celery Task Definitions
# =============================================================================

def _get_celery_app():
    """Lazy import to avoid circular imports at module load time."""
    from app.celery_app import celery_app
    return celery_app


# We define the Celery task at module level so it can be discovered by autodiscover.
# However, we need to handle the case where Celery is not configured yet.


@_get_celery_app().task(
    name="app.parsers.batch_processor.process_batch_task",
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
)
def process_batch_task(self, batch_id: str) -> Dict[str, Any]:
    """
    Celery task: process all files in a batch.

    Parameters
    ----------
    batch_id : str
        ID of the batch to process.

    Returns
    -------
    dict
        Summary with ``batch_id``, ``total``, ``completed``, ``failed``, etc.
    """
    logger.info("Celery task: processing batch %s", batch_id)

    state = _get_batch_state(batch_id)
    if state is None:
        logger.error("Batch %s not found in store", batch_id)
        return {"error": f"Batch {batch_id} not found"}

    job = BatchJob(**state["job"])
    job.status = "processing"
    job.started_at = datetime.now(timezone.utc).isoformat()
    state["job"] = job.model_dump()
    _set_batch_state(batch_id, state)

    processor = BatchProcessor()
    results = processor._process_batch_sync(batch_id)

    completed = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)

    return {
        "batch_id": batch_id,
        "status": job.status,
        "total_files": job.total_files,
        "completed": completed,
        "failed": failed,
        "completed_at": job.completed_at,
    }


@_get_celery_app().task(
    name="app.parsers.batch_processor.process_patient_folder_task",
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
)
def process_patient_folder_task(
    self,
    folder_path: str,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Celery task: process all files in a patient folder.

    Parameters
    ----------
    folder_path : str
        Path to the patient folder.
    options : dict, optional
        Batch options as a dictionary.

    Returns
    -------
    dict
        PatientBatchResult summary.
    """
    logger.info("Celery task: processing patient folder %s", folder_path)

    try:
        batch_opts = BatchOptions(**options) if options else BatchOptions()
    except Exception:
        batch_opts = BatchOptions()

    processor = BatchProcessor()
    result = processor.process_patient_folder(folder_path, batch_opts)

    return result.model_dump()


# =============================================================================
# Singleton instance
# =============================================================================

batch_processor = BatchProcessor()
