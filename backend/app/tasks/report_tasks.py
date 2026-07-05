"""
Report Generation Tasks — Celery worker implementations.

Provides async processing for:
- PDF/Excel report generation
- Daily statistics aggregation
- Cleanup of expired temporary results
- Export of data in multiple formats (CSV, JSON, Excel)
"""

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.report_tasks.generate_daily_stats",
    bind=True,
)
def generate_daily_stats(self, date_str: Optional[str] = None) -> Dict:
    """Generate daily statistics summary.

    Aggregates metrics for a given day: documents processed, corrections made,
    accuracy rates, most common error patterns.

    Args:
        date_str: Date in YYYY-MM-DD format (defaults to yesterday).

    Returns:
        Dictionary with daily statistics.
    """
    if date_str:
        target_date = date_str
    else:
        target_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info("Generating daily stats for %s", target_date)

    db = SessionLocal()
    try:
        stats = {}

        # Documents processed
        row = db.execute(
            """SELECT COUNT(*) as count,
                      COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed
               FROM documents
               WHERE DATE(created_at) = :date
            """,
            {"date": target_date},
        ).fetchone()
        stats["documents_processed"] = row["count"] if row else 0
        stats["documents_completed"] = row["completed"] if row else 0

        # Corrections made
        row = db.execute(
            """SELECT COUNT(*) as total,
                      COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved,
                      COUNT(CASE WHEN status = 'gold_standard' THEN 1 END) as gold
               FROM text_regions
               WHERE DATE(corrected_at) = :date
               AND corrected_text IS NOT NULL
            """,
            {"date": target_date},
        ).fetchone()
        stats["corrections_made"] = row["total"] if row else 0
        stats["corrections_approved"] = row["approved"] if row else 0
        stats["corrections_gold_standard"] = row["gold"] if row else 0

        # Average confidence
        row = db.execute(
            """SELECT AVG(confidence) as avg_conf
               FROM text_regions
               WHERE DATE(created_at) = :date
            """,
            {"date": target_date},
        ).fetchone()
        stats["average_confidence"] = float(row["avg_conf"]) if row and row["avg_conf"] else 0.0

        # Upsert into daily_stats table (if it exists)
        try:
            db.execute(
                """INSERT INTO daily_stats (date, documents_processed, corrections_made, avg_confidence)
                   VALUES (:date, :docs, :corrections, :conf)
                   ON CONFLICT (date) DO UPDATE SET
                     documents_processed = :docs,
                     corrections_made = :corrections,
                     avg_confidence = :conf
                """,
                {"date": target_date, "docs": stats["documents_processed"],
                 "corrections": stats["corrections_made"], "conf": stats["average_confidence"]},
            )
            db.commit()
        except Exception:
            db.rollback()
            # Table might not exist — non-critical

        return {"status": "ok", "date": target_date, "stats": stats}

    except Exception as exc:
        logger.error("Daily stats generation failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.report_tasks.cleanup_expired_results",
    bind=True,
)
def cleanup_expired_results(self, days_old: int = 7) -> Dict:
    """Clean up expired temporary files and Celery results.

    Removes old temporary files from the temp directory and cleans up
    stale Celery task results from Redis.

    Args:
        days_old: Files older than this many days will be deleted.

    Returns:
        Dictionary with cleanup statistics.
    """
    logger.info("Cleaning up expired results (older than %d days)", days_old)

    cleaned = {"temp_files": 0, "batch_outputs": 0, "errors": 0}

    # Clean temp directory
    temp_dir = settings.TEMP_DIR
    if os.path.exists(temp_dir):
        cutoff = datetime.utcnow().timestamp() - (days_old * 86400)
        for filename in os.listdir(temp_dir):
            filepath = os.path.join(temp_dir, filename)
            try:
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    cleaned["temp_files"] += 1
            except OSError:
                cleaned["errors"] += 1

    # Clean batch output directory
    batch_dir = settings.BATCH_OUTPUT_DIR
    if os.path.exists(batch_dir):
        cutoff = datetime.utcnow().timestamp() - (days_old * 86400)
        for filename in os.listdir(batch_dir):
            filepath = os.path.join(batch_dir, filename)
            try:
                if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    cleaned["batch_outputs"] += 1
            except OSError:
                cleaned["errors"] += 1

    logger.info(
        "Cleanup complete: temp=%d batch=%d errors=%d",
        cleaned["temp_files"],
        cleaned["batch_outputs"],
        cleaned["errors"],
    )

    return {"status": "ok", "cleaned": cleaned, "days_old": days_old}


@celery_app.task(
    name="app.tasks.report_tasks.generate_export",
    bind=True,
)
def generate_export(
    self,
    export_type: str = "csv",
    query: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict:
    """Generate an export of text regions in the specified format.

    Args:
        export_type: Format — csv, json, or xlsx.
        query: Optional search filter for detected/corrected text.
        date_from: Start date filter (YYYY-MM-DD).
        date_to: End date filter (YYYY-MM-DD).

    Returns:
        Dictionary with export file path and statistics.
    """
    logger.info("Generating %s export", export_type)
    self.update_state(state="GENERATING", meta={"step": "querying"})

    db = SessionLocal()
    try:
        # Build query with optional filters
        sql = """
            SELECT d.original_filename, tr.detected_text, tr.corrected_text,
                   tr.confidence, tr.status, tr.script_class, tr.created_at
            FROM text_regions tr
            JOIN pages p ON p.id = tr.page_id
            JOIN documents d ON d.id = p.document_id
            WHERE 1=1
        """
        params = {}

        if query:
            sql += " AND (tr.detected_text ILIKE :query OR tr.corrected_text ILIKE :query)"
            params["query"] = f"%{query}%"

        if date_from:
            sql += " AND tr.created_at >= :date_from"
            params["date_from"] = date_from

        if date_to:
            sql += " AND tr.created_at <= :date_to"
            params["date_to"] = date_to

        sql += " ORDER BY tr.created_at DESC LIMIT 10000"

        rows = db.execute(sql, params).fetchall()

        if not rows:
            return {"status": "ok", "rows_exported": 0, "message": "no_data"}

        self.update_state(state="GENERATING", meta={"step": "writing"})

        # Generate export file
        export_dir = os.path.join(settings.TEMP_DIR, "exports")
        os.makedirs(export_dir, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"export_{timestamp}.{export_type}"
        filepath = os.path.join(export_dir, filename)

        if export_type == "csv":
            import csv
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["filename", "detected", "corrected", "confidence", "status", "script", "created_at"],
                )
                writer.writeheader()
                for row in rows:
                    writer.writerow({
                        "filename": row["original_filename"],
                        "detected": row["detected_text"],
                        "corrected": row["corrected_text"] or "",
                        "confidence": row["confidence"],
                        "status": row["status"],
                        "script": row["script_class"] or "",
                        "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                    })

        elif export_type == "json":
            import json
            data = []
            for row in rows:
                data.append({
                    "filename": row["original_filename"],
                    "detected": row["detected_text"],
                    "corrected": row["corrected_text"] or "",
                    "confidence": float(row["confidence"]) if row["confidence"] else 0.0,
                    "status": row["status"],
                    "script_class": row["script_class"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                })
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        elif export_type == "xlsx":
            try:
                from openpyxl import Workbook
                wb = Workbook()
                ws = wb.active
                ws.title = "Export"
                ws.append(["Filename", "Detected", "Corrected", "Confidence", "Status", "Script", "Created"])
                for row in rows:
                    ws.append([
                        row["original_filename"],
                        row["detected_text"],
                        row["corrected_text"] or "",
                        row["confidence"],
                        row["status"],
                        row["script_class"] or "",
                        row["created_at"].isoformat() if row["created_at"] else "",
                    ])
                wb.save(filepath)
            except ImportError:
                return {"status": "error", "reason": "openpyxl_not_installed"}

        else:
            return {"status": "error", "reason": f"unsupported_format: {export_type}"}

        return {
            "status": "ok",
            "rows_exported": len(rows),
            "file": filepath,
            "format": export_type,
        }

    except Exception as exc:
        logger.error("Export generation failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
    finally:
        db.close()
