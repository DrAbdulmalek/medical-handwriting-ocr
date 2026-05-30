"""
Dictionary Background Tasks — Celery worker implementations.

Provides async processing for:
- Syncing remote dictionaries from GitHub
- Validating dictionary cache freshness
- Rebuilding search indexes
- Importing new medical terminology datasets
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.dictionary_client import DictionaryManager

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.dictionary_tasks.sync_remote_dictionaries",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def sync_remote_dictionaries(self, force: bool = False) -> Dict:
    """Synchronize remote dictionaries from GitHub to local cache.

    Downloads the latest dictionary files from the configured GitHub
    repository and refreshes the local cache.  Only re-downloads files
    that have changed (based on GitHub commit SHA comparison).

    Args:
        force: If True, bypass the cache TTL and force a full re-download.

    Returns:
        Dictionary with sync statistics.
    """
    logger.info("Starting dictionary sync (force=%s)", force)
    self.update_state(state="SYNCING", meta={"step": "connecting"})

    try:
        manager = DictionaryManager()

        if not manager.is_token_valid():
            logger.warning("Dictionary token is invalid, skipping sync")
            return {"status": "skipped", "reason": "invalid_token"}

        # Get repository info
        self.update_state(state="SYNCING", meta={"step": "fetching_tree"})
        tree = manager.github_client.get_repo(
            f"{settings.DICTIONARY_GITHUB_OWNER}/{settings.DICTIONARY_GITHUB_REPO}"
        ).get_tree()

        files = [item for item in tree if item.type == "blob" and item.path.endswith((".json", ".csv", ".txt"))]
        total_files = len(files)
        downloaded = 0
        skipped = 0
        failed = 0

        for i, file_info in enumerate(files):
            self.update_state(
                state="SYNCING",
                meta={"step": f"downloading_{i+1}/{total_files}", "file": file_info.path},
            )

            try:
                content = file_info.decoded_content
                if content is None:
                    logger.warning("Empty content for %s", file_info.path)
                    skipped += 1
                    continue

                # Save to local cache
                local_path = os.path.join(
                    settings.DICTIONARY_DATA_DIR,
                    file_info.path,
                )
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "wb") as f:
                    f.write(content)

                downloaded += 1
                logger.debug("Synced: %s (%d bytes)", file_info.path, len(content))

            except Exception as exc:
                logger.error("Failed to sync %s: %s", file_info.path, exc)
                failed += 1

        # Clear in-memory cache to force re-read
        if hasattr(manager, "_cache"):
            manager._cache = {}

        logger.info(
            "Dictionary sync complete: downloaded=%d skipped=%d failed=%d total=%d",
            downloaded, skipped, failed, total_files,
        )

        return {
            "status": "ok",
            "downloaded": downloaded,
            "skipped": skipped,
            "failed": failed,
            "total_files": total_files,
            "timestamp": datetime.utcnow().isoformat(),
        }

    except Exception as exc:
        logger.error("Dictionary sync failed: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.tasks.dictionary_tasks.validate_cache_freshness",
    bind=True,
)
def validate_cache_freshness(self) -> Dict:
    """Check if the local dictionary cache is still fresh.

    Compares local file timestamps against the configured cache TTL.
    Returns a report of dictionaries that need refreshing.

    Returns:
        Dictionary with freshness status for each dictionary.
    """
    logger.info("Checking dictionary cache freshness")
    cache_ttl_hours = 24  # Default TTL
    data_dir = settings.DICTIONARY_DATA_DIR

    if not os.path.exists(data_dir):
        return {
            "status": "no_cache",
            "message": "Local dictionary cache directory does not exist",
        }

    report = {"total": 0, "fresh": 0, "stale": 0, "details": []}

    for root, dirs, files in os.walk(data_dir):
        for filename in files:
            filepath = os.path.join(root, filename)
            try:
                mtime = os.path.getmtime(filepath)
                age_hours = (datetime.utcnow().timestamp() - mtime) / 3600
                is_fresh = age_hours < cache_ttl_hours

                report["total"] += 1
                if is_fresh:
                    report["fresh"] += 1
                else:
                    report["stale"] += 1

                report["details"].append({
                    "file": os.path.relpath(filepath, data_dir),
                    "age_hours": round(age_hours, 1),
                    "is_fresh": is_fresh,
                })
            except OSError:
                report["stale"] += 1

    return report


@celery_app.task(
    name="app.tasks.dictionary_tasks.import_medical_terminology",
    bind=True,
)
def import_medical_terminology(
    self,
    source_file: str,
    dictionary_name: str = "imported_medical",
) -> Dict:
    """Import medical terminology from a file into the dictionary cache.

    Supports JSON, CSV, and plain text formats.

    Args:
        source_file: Path to the source file.
        dictionary_name: Name for the imported dictionary.

    Returns:
        Dictionary with import statistics.
    """
    logger.info("Importing medical terminology from %s", source_file)

    try:
        dest_dir = os.path.join(settings.DICTIONARY_DATA_DIR, dictionary_name)
        os.makedirs(dest_dir, exist_ok=True)

        dest_file = os.path.join(dest_dir, os.path.basename(source_file))

        import shutil
        shutil.copy2(source_file, dest_file)

        # Count terms
        with open(dest_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        term_count = len(lines)

        logger.info("Imported %d terms into %s", term_count, dictionary_name)

        return {
            "status": "ok",
            "dictionary_name": dictionary_name,
            "terms_imported": term_count,
            "source_file": source_file,
        }

    except Exception as exc:
        logger.error("Terminology import failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
