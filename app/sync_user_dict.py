"""
User Dictionary Sync with Hugging Face Dataset.

Downloads the user-corrections dictionary when the Space starts and
auto-uploads after every manual correction so that nothing is lost
on Space restart / rebuild.

Usage
-----
    from app.sync_user_dict import sync_on_startup
    sync_on_startup()          # called once at app boot
"""

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class UserDictSync:
    """Bi-directional sync between /data/user_corrections.json and a
    private Hugging Face Dataset repository."""

    def __init__(
        self,
        repo_id: str = "DrAbdulmalek/medical-ocr-user-corrections",
        filename: str = "user_corrections.json",
        local_path: str = "/data/user_corrections.json",
        token: Optional[str] = None,
        auto_sync: bool = True,
        sync_interval_minutes: int = 5,
    ):
        self.repo_id = repo_id
        self.filename = filename
        self.local_path = Path(local_path)
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self.token = token or os.environ.get("HF_TOKEN")
        self.auto_sync = auto_sync
        self.sync_interval = sync_interval_minutes * 60  # seconds
        self._last_sync_time: float = 0
        self._sync_lock = threading.Lock()
        self._api = None

    # ------------------------------------------------------------------
    # Lazy HF API init (avoid importing huggingface_hub at module level)
    # ------------------------------------------------------------------
    @property
    def api(self):
        if self._api is None:
            try:
                from huggingface_hub import HfApi
                self._api = HfApi()
            except ImportError:
                logger.warning("huggingface_hub not installed — cloud sync disabled")
        return self._api

    @property
    def available(self) -> bool:
        return self._api is not None and self.token is not None

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------
    def download(self) -> bool:
        """Download dictionary from HF Dataset to local path.

        Handles two storage formats:
        - ``{"corrections": {...}, "metadata": {...}}``  (new)
        - ``{...}``  (legacy plain dict)
        """
        if not self.available:
            return False

        try:
            logger.info("Downloading user dictionary from %s ...", self.repo_id)
            from huggingface_hub import hf_hub_download

            downloaded_path = hf_hub_download(
                repo_id=self.repo_id,
                filename=self.filename,
                repo_type="dataset",
                token=self.token,
            )

            # Read and extract corrections (handle both formats)
            with open(downloaded_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict) and "corrections" in data:
                corrections = data["corrections"]
            else:
                corrections = data

            # Write as plain dict (the format the learning engine expects)
            with open(self.local_path, "w", encoding="utf-8") as f:
                json.dump(corrections, f, ensure_ascii=False, indent=2)

            count = len(corrections) if isinstance(corrections, dict) else 0
            logger.info("Downloaded %d user corrections", count)
            self._last_sync_time = datetime.now().timestamp()
            return True

        except Exception as exc:
            from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError
            if isinstance(exc, (EntryNotFoundError,)):
                logger.info("No existing dictionary on HF — starting fresh")
            elif isinstance(exc, RepositoryNotFoundError):
                logger.warning("Repository %s not found — will create on first upload", self.repo_id)
            else:
                logger.error("Download failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    def upload(self, force: bool = False) -> bool:
        """Upload local dictionary to HF Dataset.

        Wraps the plain dict in ``{"metadata": ..., "corrections": ...}``
        format for the remote file.
        """
        if not self.available or not self.local_path.exists():
            return False

        # Throttle non-forced uploads
        if not force and self.auto_sync:
            now = datetime.now().timestamp()
            if now - self._last_sync_time < self.sync_interval:
                return False

        with self._sync_lock:
            try:
                # Read local corrections
                with open(self.local_path, "r", encoding="utf-8") as f:
                    corrections = json.load(f)

                count = len(corrections) if isinstance(corrections, dict) else 0

                # Wrap with metadata for the remote file
                payload = {
                    "metadata": {
                        "last_updated": datetime.now().isoformat(),
                        "total_corrections": count,
                        "synced_from": "hf-space",
                    },
                    "corrections": corrections,
                }

                temp_path = self.local_path.with_suffix(".tmp.json")
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)

                # Ensure repo exists
                self._ensure_repo()

                # Upload
                self.api.upload_file(
                    path_or_fileobj=str(temp_path),
                    path_in_repo=self.filename,
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    token=self.token,
                    commit_message=f"Auto-sync: {count} corrections ({datetime.now():%Y-%m-%d %H:%M})",
                )

                temp_path.unlink(missing_ok=True)
                self._last_sync_time = datetime.now().timestamp()
                logger.info("Uploaded %d corrections to %s", count, self.repo_id)
                return True

            except Exception as exc:
                logger.error("Upload failed: %s", exc)
                # Clean up temp file
                temp_path = self.local_path.with_suffix(".tmp.json")
                temp_path.unlink(missing_ok=True)
                return False

    # ------------------------------------------------------------------
    # Sync (download then upload)
    # ------------------------------------------------------------------
    def sync(self) -> Dict:
        result = {"downloaded": False, "uploaded": False, "total": 0}

        result["downloaded"] = self.download()

        if self.local_path.exists():
            try:
                with open(self.local_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    result["total"] = len(data) if isinstance(data, dict) else 0
            except Exception:
                pass

        result["uploaded"] = self.upload(force=True)
        return result

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------
    def start_background_sync(self):
        if not self.auto_sync or not self.available:
            return

        def _loop():
            while True:
                time.sleep(self.sync_interval)
                try:
                    self.upload()
                except Exception as exc:
                    logger.error("Background sync failed: %s", exc)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        logger.info("Background sync started (every %d min)", self.sync_interval // 60)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _ensure_repo(self):
        """Create the private dataset repo if it doesn't exist."""
        try:
            self.api.create_repo(
                repo_id=self.repo_id,
                repo_type="dataset",
                private=True,
                exist_ok=True,
            )
        except Exception as exc:
            logger.debug("Repo ensure (may already exist): %s", exc)


# ============================================================================
# Module-level singleton & public API
# ============================================================================

_instance: Optional[UserDictSync] = None


def get_sync() -> UserDictSync:
    global _instance
    if _instance is None:
        _instance = UserDictSync(
            repo_id=os.environ.get("HF_SYNC_REPO", "DrAbdulmalek/medical-ocr-user-corrections"),
            token=os.environ.get("HF_TOKEN"),
            auto_sync=os.environ.get("HF_AUTO_SYNC", "1") == "1",
            sync_interval_minutes=int(os.environ.get("HF_SYNC_INTERVAL", "5")),
        )
    return _instance


def sync_on_startup() -> Dict:
    """Call once at app boot: downloads dict + starts background sync."""
    sync = get_sync()
    result = sync.sync()
    logger.info("Startup sync: %s", result)
    sync.start_background_sync()
    return result


def sync_after_learning() -> bool:
    """Call after every manual correction: uploads immediately."""
    return get_sync().upload(force=True)