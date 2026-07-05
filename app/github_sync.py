"""
GitHub Sync Module — HF Space <-> Private Repos

Three-repo architecture:
  1. DrAbdulmalek/arabic-dictionaries-collection (PRIVATE) — Medical dictionaries
  2. DrAbdulmalek/medical-ocr-work-data (PRIVATE) — Corrections, training data, work logs
  3. DrAbdulmalek/medical-handwriting-ocr (PUBLIC) — Main project (references private repos)

All private repo access requires GITHUB_TOKEN (set in HF Space secrets).
"""

import json
import logging
import os
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
_OWNER = "DrAbdulmalek"

# Private repos
DICT_REPO = "arabic-dictionaries-collection"
WORK_REPO = "medical-ocr-work-data"

# Public repo (for references only)
PUBLIC_REPO = "medical-handwriting-ocr"

# Local cache dirs (persist on HF Space via /data)
_CACHE_DIR = Path("/data/github_sync")
_DICT_CACHE = _CACHE_DIR / "dictionaries"
_WORK_CACHE = _CACHE_DIR / "work_data"
_DICT_CACHE.mkdir(parents=True, exist_ok=True)
_WORK_CACHE.mkdir(parents=True, exist_ok=True)


def _headers() -> Dict[str, str]:
    if not _GITHUB_TOKEN:
        return {"Accept": "application/vnd.github.v3+json"}
    return {
        "Authorization": f"token {_GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def _is_configured() -> bool:
    return bool(_GITHUB_TOKEN and _GITHUB_TOKEN.startswith("ghp_"))


# ---------------------------------------------------------------------------
# Dictionary Repo Operations
# ---------------------------------------------------------------------------

def load_dictionary_terms() -> Optional[set]:
    """Load all terms from the private dictionary repo.

    Downloads all JSON files from the dictionary repo (root + subdirectories),
    extracts terms, caches locally in /data/github_sync/dictionaries/.
    Returns a set of term strings, or None if not configured.
    """
    if not _is_configured():
        logger.info("Dictionary: GITHUB_TOKEN not set — skipping")
        return None

    cache_file = _DICT_CACHE / "all_terms.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                terms = set(json.load(f))
            logger.info("Dictionary: loaded %d terms from cache", len(terms))
            return terms
        except Exception as exc:
            logger.warning("Dictionary: cache read failed: %s", exc)

    # Download from GitHub
    try:
        import requests
        terms = _download_all_dict_files(requests)
        if terms:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(list(terms), f, ensure_ascii=False)
            logger.info("Dictionary: downloaded %d terms from GitHub", len(terms))
            return terms
    except Exception as exc:
        logger.error("Dictionary: download failed: %s", exc)

    return None


def _download_all_dict_files(requests_mod) -> set:
    """Recursively download all JSON files from the dictionary repo."""
    all_terms = set()
    files_processed = 0

    def process_dir(path: str):
        nonlocal files_processed
        url = f"https://api.github.com/repos/{_OWNER}/{DICT_REPO}/contents/{path}"
        resp = requests_mod.get(url, headers=_headers(), timeout=30)
        if resp.status_code != 200:
            logger.warning("Dictionary: failed to list %s (HTTP %d)", path, resp.status_code)
            return
        for item in resp.json():
            if item["type"] == "file" and item["name"].endswith(".json"):
                try:
                    dl = requests_mod.get(item["download_url"], headers=_headers(), timeout=60)
                    if dl.status_code == 200:
                        data = dl.json()
                        extracted = _extract_terms(data)
                        all_terms.update(extracted)
                        files_processed += 1
                        if extracted:
                            logger.info("  Dict: %s → %d terms", item["name"], len(extracted))
                except Exception:
                    pass
            elif item["type"] == "dir":
                process_dir(item["path"])

    process_dir("")
    return all_terms


def _extract_terms(data) -> set:
    """Extract terms from various JSON structures."""
    terms = set()
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                for key in ("term", "word", "name", "arabic", "ar", "headword"):
                    if key in entry and entry[key]:
                        terms.add(str(entry[key]))
            elif isinstance(entry, str) and entry.strip():
                terms.add(entry.strip())
    elif isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, str) and val.strip():
                terms.add(key.strip())
                terms.add(val.strip())
            elif isinstance(val, dict):
                for k2 in ("term", "word", "name", "definition", "meaning"):
                    if k2 in val and val[k2]:
                        terms.add(str(val[k2]))
    return terms


# ---------------------------------------------------------------------------
# Work Data Repo Operations (corrections, training, logs)
# ---------------------------------------------------------------------------

def save_correction_to_github(correction_data: Dict) -> Dict:
    """Save a single correction to the private work-data repo.

    Creates/updates a daily JSONL file: corrections/YYYY-MM-DD.jsonl
    """
    if not _is_configured():
        return {"status": "error", "message": "GITHUB_TOKEN not configured"}

    try:
        import requests
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file_path = f"corrections/{today}.jsonl"

        # Read existing content
        existing_content = _read_github_file(WORK_REPO, file_path)
        existing_lines = []
        if existing_content:
            try:
                existing_lines = existing_content.split("\n")
                existing_lines = [l for l in existing_lines if l.strip()]
            except Exception:
                pass

        # Append new entry
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "hf_space",
            **correction_data,
        }
        existing_lines.append(json.dumps(entry, ensure_ascii=False))
        new_content = "\n".join(existing_lines)

        # Commit to GitHub
        commit_msg = f"correction: {correction_data.get('original_text', '?')} → {correction_data.get('corrected_text', '?')}"
        result = _write_github_file(WORK_REPO, file_path, new_content, commit_msg)
        return result

    except Exception as exc:
        logger.error("save_correction_to_github failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def save_corrections_batch_to_github(corrections: List[Dict]) -> Dict:
    """Save a batch of corrections to the private work-data repo."""
    if not _is_configured():
        return {"status": "error", "message": "GITHUB_TOKEN not configured"}

    saved = 0
    errors = 0
    for corr in corrections:
        if corr.get("original_text") != corr.get("corrected_text"):
            result = save_correction_to_github(corr)
            if result.get("status") == "success":
                saved += 1
            else:
                errors += 1

    return {
        "status": "success",
        "saved": saved,
        "errors": errors,
        "total": len(corrections),
    }


def export_training_data_to_github(training_jsonl: str) -> Dict:
    """Export training-ready JSONL data to the private work-data repo.

    Saves to: training_exports/YYYYMMDD_HHMMSS.jsonl
    """
    if not _is_configured():
        return {"status": "error", "message": "GITHUB_TOKEN not configured"}

    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        file_path = f"training_exports/{ts}.jsonl"
        commit_msg = f"training export: {len(training_jsonl.splitlines())} entries"

        # Also save locally
        local_path = _WORK_CACHE / "training_exports"
        local_path.mkdir(parents=True, exist_ok=True)
        (local_path / f"{ts}.jsonl").write_text(training_jsonl, encoding="utf-8")

        result = _write_github_file(WORK_REPO, file_path, training_jsonl, commit_msg)
        return result

    except Exception as exc:
        logger.error("export_training_data_to_github failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def save_work_log(message: str, category: str = "general", data: Optional[Dict] = None) -> Dict:
    """Append a work log entry to the private work-data repo.

    Saves to: logs/YYYY-MM-DD.jsonl
    """
    if not _is_configured():
        return {"status": "skipped", "message": "GITHUB_TOKEN not configured"}

    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file_path = f"logs/{today}.jsonl"

        existing_content = _read_github_file(WORK_REPO, file_path)
        existing_lines = []
        if existing_content:
            try:
                existing_lines = [l for l in existing_content.split("\n") if l.strip()]
            except Exception:
                pass

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "message": message,
        }
        if data:
            entry["data"] = data

        existing_lines.append(json.dumps(entry, ensure_ascii=False))
        new_content = "\n".join(existing_lines)

        result = _write_github_file(WORK_REPO, file_path, new_content,
                                    f"log [{category}]: {message[:60]}")
        return result

    except Exception as exc:
        logger.warning("save_work_log failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def get_work_stats() -> Dict:
    """Get statistics from the private work-data repo."""
    if not _is_configured():
        return {"status": "not_configured", "message": "Set GITHUB_TOKEN in HF Space secrets"}

    try:
        import requests
        stats = {
            "repo": f"{_OWNER}/{WORK_REPO}",
            "corrections_files": 0,
            "training_exports": 0,
            "log_files": 0,
            "total_corrections": 0,
        }

        for folder in ["corrections", "training_exports", "logs"]:
            url = f"https://api.github.com/repos/{_OWNER}/{WORK_REPO}/contents/{folder}"
            resp = requests.get(url, headers=_headers(), timeout=15)
            if resp.status_code == 200:
                items = resp.json()
                stats[f"{folder}_count" if folder != "corrections" else "corrections_files"] = len(items)
                if folder == "corrections":
                    for item in items:
                        # Count lines in each JSONL file
                        dl = requests.get(item["download_url"], headers=_headers(), timeout=30)
                        if dl.status_code == 200:
                            lines = [l for l in dl.text.split("\n") if l.strip()]
                            stats["total_corrections"] += len(lines)

        return {"status": "ready", **stats}

    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# GitHub API Helpers
# ---------------------------------------------------------------------------

def _read_github_file(repo: str, path: str) -> Optional[str]:
    """Read a file from a GitHub repo via API."""
    import requests
    url = f"https://api.github.com/repos/{_OWNER}/{repo}/contents/{path}"
    resp = requests.get(url, headers=_headers(), timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8")
        elif data.get("download_url"):
            dl = requests.get(data["download_url"], headers=_headers(), timeout=30)
            if dl.status_code == 200:
                return dl.text
    return None


def _write_github_file(repo: str, path: str, content: str, message: str) -> Dict:
    """Write/update a file in a GitHub repo via API."""
    import requests
    url = f"https://api.github.com/repos/{_OWNER}/{repo}/contents/{path}"

    # Get current SHA (needed for updates)
    sha = None
    resp = requests.get(url, headers=_headers(), timeout=15)
    if resp.status_code == 200:
        sha = resp.json().get("sha")

    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, headers=_headers(), json=payload, timeout=30)
    if resp.status_code in (200, 201):
        return {"status": "success", "path": path, "action": "updated" if sha else "created"}
    else:
        error = resp.json().get("message", f"HTTP {resp.status_code}")
        return {"status": "error", "message": error}


# ---------------------------------------------------------------------------
# Public Repo References (updates README in public repo)
# ---------------------------------------------------------------------------

def get_private_repos_info() -> Dict:
    """Return info about the private repos for display in the public project."""
    return {
        "dictionaries": {
            "repo": f"{_OWNER}/{DICT_REPO}",
            "description": "مجموعة القواميس العربية الطبية — Arabic Medical Dictionaries",
            "access": "Requires GITHUB_TOKEN",
            "usage": "Source of medical terms for OCR auto-correction",
        },
        "work_data": {
            "repo": f"{_OWNER}/{WORK_REPO}",
            "description": "نواتج العمل وبيانات التدريب — Work Outputs & Training Data",
            "access": "Requires GITHUB_TOKEN",
            "usage": "Stores corrections, training exports, work logs",
        },
        "public_project": {
            "repo": f"{_OWNER}/{PUBLIC_REPO}",
            "description": "Medical Handwriting OCR — Multi-Engine (Public)",
            "access": "Public — no token required",
            "usage": "Main project with HF Space deployment",
        },
    }


def get_sync_status() -> Dict:
    """Get full sync status for all repos."""
    result = {
        "github_token": bool(_GITHUB_TOKEN),
        "repos": {},
    }

    if not _is_configured():
        result["message"] = "Set GITHUB_TOKEN in HF Space secrets to enable sync"
        return result

    try:
        import requests
        for repo_name, label in [
            (DICT_REPO, "dictionaries"),
            (WORK_REPO, "work_data"),
        ]:
            url = f"https://api.github.com/repos/{_OWNER}/{repo_name}"
            resp = requests.get(url, headers=_headers(), timeout=10)
            if resp.status_code == 200:
                d = resp.json()
                result["repos"][label] = {
                    "status": "accessible",
                    "private": d.get("private", False),
                    "url": d.get("html_url"),
                }
            else:
                result["repos"][label] = {"status": f"HTTP {resp.status_code}"}
    except Exception as exc:
        result["error"] = str(exc)

    return result