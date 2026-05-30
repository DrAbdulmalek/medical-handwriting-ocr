"""
Data Retention Task Module for Medical Handwriting OCR.

This module defines Celery tasks that enforce the project's data retention
policy.  Each task is responsible for a specific cleanup domain:

* **Documents** – delete ``Document`` rows (and their dependent ``Page``
  and ``TextRegion`` rows) whose ``created_at`` timestamp is older than the
  configured retention window.

* **API Keys** – deactivate (soft-delete) or hard-delete ``APIKey`` rows
  whose ``expires_at`` timestamp is in the past.

* **Audit Logs** – purge ``AuditLog`` rows older than the configured
  retention window.

* **Orphaned Crops** – scan the ``crops/`` prefix in MinIO and remove
  objects that have not been modified within the configured grace period.

* **Retention Report** – generate a summary dict of counts that *would*
  be affected by each cleanup rule, useful for previewing a dry-run.

* **Run Retention Policy** – orchestrate all cleanup tasks in sequence
  and return an aggregated result.

Environment Variables
---------------------
RETENTION_DOCUMENT_DAYS : int, default ``90``
    Number of days after which documents are eligible for deletion.

RETENTION_AUDIT_DAYS : int, default ``365``
    Number of days after which audit logs are eligible for deletion.

RETENTION_ORPHAN_CROP_DAYS : int, default ``30``
    Number of days after which unreferenced crop images are eligible for
    deletion.

RETENTION_DRY_RUN : str (``"true"`` / ``"false"``), default ``"false"``
    When ``"true"``, no data is actually deleted; tasks only log and
    report what *would* be removed.

Notes
-----
All tasks operate within their own SQLAlchemy sessions obtained from
``app.database.SessionLocal`` to avoid conflicts with the request-cycle
ORM lifecycle.
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, delete, func, and_
from sqlalchemy.orm import selectinload

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Document, Page, TextRegion, APIKey, AuditLog
from app.storage import storage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

_DOCUMENT_DAYS_DEFAULT: int = 90
_AUDIT_DAYS_DEFAULT: int = 365
_ORPHAN_CROP_DAYS_DEFAULT: int = 30


def _get_retention_document_days() -> int:
    """Return the document retention window in days (from env or default)."""
    try:
        return int(os.environ.get("RETENTION_DOCUMENT_DAYS", _DOCUMENT_DAYS_DEFAULT))
    except (ValueError, TypeError):
        logger.warning(
            "Invalid RETENTION_DOCUMENT_DAYS value; falling back to %d",
            _DOCUMENT_DAYS_DEFAULT,
        )
        return _DOCUMENT_DAYS_DEFAULT


def _get_retention_audit_days() -> int:
    """Return the audit-log retention window in days (from env or default)."""
    try:
        return int(os.environ.get("RETENTION_AUDIT_DAYS", _AUDIT_DAYS_DEFAULT))
    except (ValueError, TypeError):
        logger.warning(
            "Invalid RETENTION_AUDIT_DAYS value; falling back to %d",
            _AUDIT_DAYS_DEFAULT,
        )
        return _AUDIT_DAYS_DEFAULT


def _get_retention_orphan_crop_days() -> int:
    """Return the orphan-crop grace period in days (from env or default)."""
    try:
        return int(
            os.environ.get("RETENTION_ORPHAN_CROP_DAYS", _ORPHAN_CROP_DAYS_DEFAULT)
        )
    except (ValueError, TypeError):
        logger.warning(
            "Invalid RETENTION_ORPHAN_CROP_DAYS value; falling back to %d",
            _ORPHAN_CROP_DAYS_DEFAULT,
        )
        return _ORPHAN_CROP_DAYS_DEFAULT


def _is_dry_run() -> bool:
    """Return ``True`` when ``RETENTION_DRY_RUN`` is set to a truthy value."""
    return os.environ.get("RETENTION_DRY_RUN", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Cleanup: Old Documents
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.retention.cleanup_old_documents",
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
)
def cleanup_old_documents(
    self, days: Optional[int] = None
) -> Dict[str, Any]:
    """Delete documents older than *days* days, cascading to pages and text regions.

    Parameters
    ----------
    days : int, optional
        Override for the document retention window.  When ``None`` the value
        is read from the ``RETENTION_DOCUMENT_DAYS`` environment variable
        (default 90).

    Returns
    -------
    dict
        A summary containing ``deleted_documents``, ``deleted_pages``,
        ``deleted_regions``, ``dry_run``, and ``cutoff``.
    """
    retention_days = days if days is not None else _get_retention_document_days()
    dry_run = _is_dry_run()
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    logger.info(
        "cleanup_old_documents: days=%s, cutoff=%s, dry_run=%s",
        retention_days,
        cutoff.isoformat(),
        dry_run,
    )

    deleted_documents = 0
    deleted_pages = 0
    deleted_regions = 0

    session = SessionLocal()
    try:
        # --- Gather IDs of documents to delete --------------------------------
        stmt = select(Document.id).where(Document.created_at < cutoff)
        doc_ids: List = list(session.scalars(stmt).all())

        if not doc_ids:
            logger.info("cleanup_old_documents: no documents older than %s", cutoff)
            return {
                "deleted_documents": 0,
                "deleted_pages": 0,
                "deleted_regions": 0,
                "dry_run": dry_run,
                "cutoff": cutoff.isoformat(),
            }

        # --- Count cascading rows (for reporting) ----------------------------
        deleted_documents = len(doc_ids)

        page_count_result = session.scalar(
            select(func.count(Page.id)).where(Page.document_id.in_(doc_ids))
        )
        deleted_pages = int(page_count_result) if page_count_result else 0

        region_count_result = session.scalar(
            select(func.count(TextRegion.id)).where(
                TextRegion.page_id.in_(
                    select(Page.id).where(Page.document_id.in_(doc_ids))
                )
            )
        )
        deleted_regions = int(region_count_result) if region_count_result else 0

        if dry_run:
            logger.info(
                "DRY RUN: would delete %d documents, %d pages, %d regions",
                deleted_documents,
                deleted_pages,
                deleted_regions,
            )
        else:
            # Delete text_regions first (child), then pages, then documents
            # to respect FK constraints without relying on DB-level cascade.
            session.execute(
                delete(TextRegion).where(
                    TextRegion.page_id.in_(
                        select(Page.id).where(Page.document_id.in_(doc_ids))
                    )
                )
            )
            session.execute(
                delete(Page).where(Page.document_id.in_(doc_ids))
            )
            session.execute(
                delete(Document).where(Document.id.in_(doc_ids))
            )
            session.commit()
            logger.info(
                "Deleted %d documents, %d pages, %d regions",
                deleted_documents,
                deleted_pages,
                deleted_regions,
            )

    except Exception as exc:
        session.rollback()
        logger.error("cleanup_old_documents failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)
    finally:
        session.close()

    return {
        "deleted_documents": deleted_documents,
        "deleted_pages": deleted_pages,
        "deleted_regions": deleted_regions,
        "dry_run": dry_run,
        "cutoff": cutoff.isoformat(),
    }


# ---------------------------------------------------------------------------
# Cleanup: Expired API Keys
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.retention.cleanup_expired_api_keys",
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
)
def cleanup_expired_api_keys(self) -> Dict[str, Any]:
    """Deactivate or delete API keys whose ``expires_at`` timestamp has passed.

    Expired keys are **soft-deleted** (``is_active`` set to ``False``) so that
    the record is preserved for audit purposes.  If the key has been inactive
    for more than 90 days *after* expiry it is hard-deleted.

    Returns
    -------
    dict
        Summary with ``deactivated``, ``deleted``, and ``dry_run``.
    """
    dry_run = _is_dry_run()
    now = datetime.now(timezone.utc)
    hard_delete_cutoff = now - timedelta(days=90)

    logger.info(
        "cleanup_expired_api_keys: now=%s, dry_run=%s", now.isoformat(), dry_run
    )

    deactivated = 0
    deleted = 0

    session = SessionLocal()
    try:
        # --- Soft-delete: deactivate expired keys that are still active ---------
        expired_active = session.scalars(
            select(APIKey).where(
                and_(
                    APIKey.expires_at.isnot(None),
                    APIKey.expires_at < now,
                    APIKey.is_active.is_(True),
                )
            )
        ).all()

        deactivated = len(expired_active)
        if not dry_run:
            for key in expired_active:
                key.is_active = False
                logger.info("Deactivated expired API key %s (%s)", key.id, key.name)
            # flush so changes are visible for the next query
            session.flush()

        # --- Hard-delete: remove keys expired >90 days ago --------------------
        stale_keys = session.scalars(
            select(APIKey).where(
                and_(
                    APIKey.expires_at.isnot(None),
                    APIKey.expires_at < hard_delete_cutoff,
                    APIKey.is_active.is_(False),
                )
            )
        ).all()

        stale_ids = [k.id for k in stale_keys]
        deleted = len(stale_ids)

        if dry_run:
            logger.info(
                "DRY RUN: would deactivate %d keys and delete %d stale keys",
                deactivated,
                deleted,
            )
        else:
            if stale_ids:
                session.execute(
                    delete(APIKey).where(APIKey.id.in_(stale_ids))
                )
            session.commit()
            logger.info(
                "Deactivated %d expired API keys, deleted %d stale keys",
                deactivated,
                deleted,
            )

    except Exception as exc:
        session.rollback()
        logger.error("cleanup_expired_api_keys failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)
    finally:
        session.close()

    return {
        "deactivated": deactivated,
        "deleted": deleted,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Cleanup: Old Audit Logs
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.retention.cleanup_audit_logs",
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
)
def cleanup_audit_logs(self, days: Optional[int] = None) -> Dict[str, Any]:
    """Delete audit log entries older than *days* days.

    Parameters
    ----------
    days : int, optional
        Override for the audit-log retention window.  When ``None`` the value
        is read from ``RETENTION_AUDIT_DAYS`` (default 365).

    Returns
    -------
    dict
        Summary with ``deleted_logs``, ``dry_run``, and ``cutoff``.
    """
    retention_days = days if days is not None else _get_retention_audit_days()
    dry_run = _is_dry_run()
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    logger.info(
        "cleanup_audit_logs: days=%s, cutoff=%s, dry_run=%s",
        retention_days,
        cutoff.isoformat(),
        dry_run,
    )

    deleted_logs = 0

    session = SessionLocal()
    try:
        # Count first for reporting
        count_result = session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.created_at < cutoff)
        )
        deleted_logs = int(count_result) if count_result else 0

        if dry_run:
            logger.info(
                "DRY RUN: would delete %d audit logs older than %s",
                deleted_logs,
                cutoff.isoformat(),
            )
        elif deleted_logs > 0:
            session.execute(
                delete(AuditLog).where(AuditLog.created_at < cutoff)
            )
            session.commit()
            logger.info("Deleted %d audit logs older than %s", deleted_logs, cutoff.isoformat())

    except Exception as exc:
        session.rollback()
        logger.error("cleanup_audit_logs failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)
    finally:
        session.close()

    return {
        "deleted_logs": deleted_logs,
        "dry_run": dry_run,
        "cutoff": cutoff.isoformat(),
    }


# ---------------------------------------------------------------------------
# Cleanup: Orphaned Crops (MinIO)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.retention.cleanup_orphaned_crops",
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
)
def cleanup_orphaned_crops(self, days: Optional[int] = None) -> Dict[str, Any]:
    """Delete crop images from MinIO that are older than *days* days.

    The task lists all objects under the ``crops/`` prefix in the configured
    MinIO bucket and removes any whose last-modified timestamp predates the
    grace period.  This ensures crop images that are no longer actively
    referenced do not accumulate indefinitely.

    Parameters
    ----------
    days : int, optional
        Override for the orphan-crop grace period.  When ``None`` the value
        is read from ``RETENTION_ORPHAN_CROP_DAYS`` (default 30).

    Returns
    -------
    dict
        Summary with ``scanned``, ``deleted``, ``failed``, ``dry_run``, and
        ``cutoff``.
    """
    grace_days = days if days is not None else _get_retention_orphan_crop_days()
    dry_run = _is_dry_run()
    cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)

    logger.info(
        "cleanup_orphaned_crops: days=%s, cutoff=%s, dry_run=%s",
        grace_days,
        cutoff.isoformat(),
        dry_run,
    )

    scanned = 0
    deleted = 0
    failed = 0
    errors: List[str] = []

    try:
        objects = list(
            storage.client.list_objects(
                storage.bucket, prefix="crops/", recursive=True
            )
        )
    except Exception as exc:
        logger.error("Failed to list MinIO objects: %s", exc, exc_info=True)
        raise self.retry(exc=exc)

    scanned = len(objects)

    for obj in objects:
        # MinIO ``last_modified`` is a naive UTC datetime in some clients;
        # normalise to timezone-aware for comparison.
        obj_time = obj.last_modified
        if obj_time.tzinfo is None:
            obj_time = obj_time.replace(tzinfo=timezone.utc)

        if obj_time >= cutoff:
            continue  # still within grace period

        object_name = obj.object_name

        if dry_run:
            logger.debug("DRY RUN: would delete orphaned crop %s", object_name)
            deleted += 1
            continue

        try:
            storage.client.remove_object(storage.bucket, object_name)
            logger.info("Deleted orphaned crop: %s", object_name)
            deleted += 1
        except Exception as exc:
            failed += 1
            msg = f"Failed to delete {object_name}: {exc}"
            errors.append(msg)
            logger.warning(msg)

    logger.info(
        "cleanup_orphaned_crops complete: scanned=%d, deleted=%d, failed=%d, dry_run=%s",
        scanned,
        deleted,
        failed,
        dry_run,
    )

    return {
        "scanned": scanned,
        "deleted": deleted,
        "failed": failed,
        "errors": errors[:50],  # cap to avoid oversized results
        "dry_run": dry_run,
        "cutoff": cutoff.isoformat(),
    }


# ---------------------------------------------------------------------------
# Retention Report (dry-run summary)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.retention.generate_retention_report",
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
)
def generate_retention_report(self) -> Dict[str, Any]:
    """Generate statistics about what would be cleaned up by the retention policy.

    This task performs **read-only** queries and a lightweight MinIO listing
    to produce a report.  No data is deleted regardless of the
    ``RETENTION_DRY_RUN`` setting.

    Returns
    -------
    dict
        A report with the following keys:

        * ``documents`` – dict with ``count``, ``pages``, ``regions``, ``cutoff``
        * ``api_keys`` – dict with ``expired_count``, ``stale_count``
        * ``audit_logs`` – dict with ``count``, ``cutoff``
        * ``orphaned_crops`` – dict with ``count``, ``cutoff``
        * ``generated_at`` – ISO-8601 timestamp of report generation
    """
    logger.info("Generating retention report (read-only) …")

    doc_days = _get_retention_document_days()
    audit_days = _get_retention_audit_days()
    crop_days = _get_retention_orphan_crop_days()
    now = datetime.now(timezone.utc)
    doc_cutoff = now - timedelta(days=doc_days)
    audit_cutoff = now - timedelta(days=audit_days)
    crop_cutoff = now - timedelta(days=crop_days)
    stale_key_cutoff = now - timedelta(days=90)

    report: Dict[str, Any] = {
        "configuration": {
            "RETENTION_DOCUMENT_DAYS": doc_days,
            "RETENTION_AUDIT_DAYS": audit_days,
            "RETENTION_ORPHAN_CROP_DAYS": crop_days,
            "RETENTION_DRY_RUN": _is_dry_run(),
        },
        "generated_at": now.isoformat(),
    }

    session = SessionLocal()
    try:
        # --- Documents -------------------------------------------------------
        doc_count = session.scalar(
            select(func.count(Document.id)).where(Document.created_at < doc_cutoff)
        ) or 0

        page_count = session.scalar(
            select(func.count(Page.id)).where(
                Page.document_id.in_(
                    select(Document.id).where(Document.created_at < doc_cutoff)
                )
            )
        ) or 0

        region_count = session.scalar(
            select(func.count(TextRegion.id)).where(
                TextRegion.page_id.in_(
                    select(Page.id).where(
                        Page.document_id.in_(
                            select(Document.id).where(Document.created_at < doc_cutoff)
                        )
                    )
                )
            )
        ) or 0

        report["documents"] = {
            "count": int(doc_count),
            "pages": int(page_count),
            "regions": int(region_count),
            "cutoff": doc_cutoff.isoformat(),
        }

        # --- API Keys ---------------------------------------------------------
        expired_count = session.scalar(
            select(func.count(APIKey.id)).where(
                and_(
                    APIKey.expires_at.isnot(None),
                    APIKey.expires_at < now,
                    APIKey.is_active.is_(True),
                )
            )
        ) or 0

        stale_count = session.scalar(
            select(func.count(APIKey.id)).where(
                and_(
                    APIKey.expires_at.isnot(None),
                    APIKey.expires_at < stale_key_cutoff,
                    APIKey.is_active.is_(False),
                )
            )
        ) or 0

        report["api_keys"] = {
            "expired_count": int(expired_count),
            "stale_count": int(stale_count),
        }

        # --- Audit Logs -------------------------------------------------------
        audit_count = session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.created_at < audit_cutoff)
        ) or 0

        report["audit_logs"] = {
            "count": int(audit_count),
            "cutoff": audit_cutoff.isoformat(),
        }

    except Exception as exc:
        logger.error("generate_retention_report DB queries failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)
    finally:
        session.close()

    # --- Orphaned Crops (MinIO) ----------------------------------------------
    try:
        objects = list(
            storage.client.list_objects(
                storage.bucket, prefix="crops/", recursive=True
            )
        )
        orphan_count = sum(
            1
            for obj in objects
            if (
                obj.last_modified
                if obj.last_modified.tzinfo
                else obj.last_modified.replace(tzinfo=timezone.utc)
            )
            < crop_cutoff
        )
    except Exception as exc:
        logger.warning("Could not list MinIO crops for report: %s", exc)
        orphan_count = -1  # sentinel for "unknown"

    report["orphaned_crops"] = {
        "count": orphan_count,
        "cutoff": crop_cutoff.isoformat(),
    }

    logger.info("Retention report generated: %s", report)
    return report


# ---------------------------------------------------------------------------
# Orchestrator: Run Full Retention Policy
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.retention.run_retention_policy",
    bind=True,
    acks_late=True,
    max_retries=1,
    default_retry_delay=120,
)
def run_retention_policy(self) -> Dict[str, Any]:
    """Execute all retention cleanup tasks in sequence and return an aggregated result.

    This is the **main entry point** for scheduled execution (e.g. via Celery
    Beat).  It invokes each individual cleanup task synchronously within the
    same worker process so that errors in one step do not silently skip others.

    Returns
    -------
    dict
        An aggregated result with sub-keys ``documents``, ``api_keys``,
        ``audit_logs``, ``orphaned_crops``, and ``summary``.
    """
    logger.info("=" * 60)
    logger.info("Starting full retention policy run (dry_run=%s)", _is_dry_run())
    logger.info("=" * 60)

    start = datetime.now(timezone.utc)

    results: Dict[str, Any] = {}

    # 1. Documents
    try:
        results["documents"] = cleanup_old_documents()
    except Exception as exc:
        logger.error("Document cleanup failed: %s", exc, exc_info=True)
        results["documents"] = {"error": str(exc)}

    # 2. API Keys
    try:
        results["api_keys"] = cleanup_expired_api_keys()
    except Exception as exc:
        logger.error("API key cleanup failed: %s", exc, exc_info=True)
        results["api_keys"] = {"error": str(exc)}

    # 3. Audit Logs
    try:
        results["audit_logs"] = cleanup_audit_logs()
    except Exception as exc:
        logger.error("Audit log cleanup failed: %s", exc, exc_info=True)
        results["audit_logs"] = {"error": str(exc)}

    # 4. Orphaned Crops
    try:
        results["orphaned_crops"] = cleanup_orphaned_crops()
    except Exception as exc:
        logger.error("Orphaned crop cleanup failed: %s", exc, exc_info=True)
        results["orphaned_crops"] = {"error": str(exc)}

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    # Build a concise human-readable summary
    doc_res = results.get("documents", {})
    key_res = results.get("api_keys", {})
    audit_res = results.get("audit_logs", {})
    crop_res = results.get("orphaned_crops", {})

    summary_lines = [
        f"Retention policy completed in {elapsed:.2f}s (dry_run={_is_dry_run()}).",
        f"  Documents: {doc_res.get('deleted_documents', 0)} deleted "
        f"({doc_res.get('deleted_pages', 0)} pages, "
        f"{doc_res.get('deleted_regions', 0)} regions)",
        f"  API Keys:  {key_res.get('deactivated', 0)} deactivated, "
        f"{key_res.get('deleted', 0)} hard-deleted",
        f"  Audit Logs: {audit_res.get('deleted_logs', 0)} deleted",
        f"  Crops:     {crop_res.get('deleted', 0)} deleted "
        f"(scanned {crop_res.get('scanned', 0)}, "
        f"failed {crop_res.get('failed', 0)})",
    ]

    summary = "\n".join(summary_lines)
    results["summary"] = summary
    results["elapsed_seconds"] = elapsed
    results["started_at"] = start.isoformat()
    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    results["dry_run"] = _is_dry_run()

    logger.info("\n%s", summary)
    logger.info("=" * 60)

    return results
