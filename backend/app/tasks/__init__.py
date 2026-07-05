"""
Data Retention Tasks for Medical Handwriting OCR.

This package provides Celery tasks that enforce the project's data
retention policy, including automated cleanup of old documents,
expired API keys, stale audit logs, and orphaned crop images stored
in MinIO object storage.
"""

from app.tasks.retention import (
    cleanup_old_documents,
    cleanup_expired_api_keys,
    cleanup_audit_logs,
    cleanup_orphaned_crops,
    generate_retention_report,
    run_retention_policy,
)

__all__ = [
    "cleanup_old_documents",
    "cleanup_expired_api_keys",
    "cleanup_audit_logs",
    "cleanup_orphaned_crops",
    "generate_retention_report",
    "run_retention_policy",
]
