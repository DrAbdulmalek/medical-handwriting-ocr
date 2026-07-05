"""
Celery application configuration for async background tasks.
Handles: OCR processing, dictionary validation, weekly training, report generation.
"""

from celery import Celery
from celery.schedules import crontab
from app.config import settings

celery_app = Celery(
    "medical_ocr",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Damascus",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    task_soft_time_limit=3300,
    worker_prefetch_multiplier=1,
    result_expires=86400,  # Results expire after 24h
)

# Auto-discover tasks from backend modules
celery_app.autodiscover_tasks([
    "app.tasks.ocr_tasks",
    "app.tasks.dictionary_tasks",
    "app.tasks.training_tasks",
    "app.tasks.report_tasks",
])

# Scheduled tasks (Celery Beat)
celery_app.conf.beat_schedule = {
    "weekly-model-training": {
        "task": "app.tasks.training_tasks.run_weekly_training",
        "schedule": crontab(hour=2, minute=0, day_of_week=0),  # Sunday 2 AM
        "args": [],
    },
    "export-training-dataset": {
        "task": "app.tasks.training_tasks.export_dataset_for_training",
        "schedule": crontab(hour=1, minute=0, day_of_week=0),  # Sunday 1 AM (before training)
        "args": [],
    },
    "cleanup-expired-results": {
        "task": "app.tasks.report_tasks.cleanup_expired_results",
        "schedule": crontab(hour=3, minute=0),  # Daily 3 AM
        "args": [],
    },
    "sync-dictionaries": {
        "task": "app.tasks.dictionary_tasks.sync_remote_dictionaries",
        "schedule": crontab(hour=4, minute=0, day_of_week=1),  # Monday 4 AM (weekly)
        "args": [],
    },
}
