"""
API Key authentication middleware for Medical Handwriting OCR API.

Authentication flow:
    1. Client sends ``X-API-Key`` header or ``api_key`` query parameter.
    2. Middleware SHA-256 hashes the raw key.
    3. Hash is looked up in the ``api_keys`` database table.
    4. Checks: key exists, is active, has not expired.
    5. Enforces per-key rate limit (stored in ``api_keys.rate_limit``).
    6. Updates ``last_used_at`` timestamp.
    7. Stores validated key info on ``request.state.api_key``.

Admin bypass:
    Requests with a valid ``X-Admin-Token`` header (value matches
    ``ADMIN_TOKEN`` env var) skip key validation entirely.

Configuration (via environment variables):
    ADMIN_TOKEN              – Secret token for admin bypass (default: "")
    API_KEY_AUTH_ENABLED     – Enable/disable API key auth (default: "true")
    BYPASS_PATHS             – Comma-separated paths that skip auth (default: "/health,/docs,/openapi.json")
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "")
API_KEY_AUTH_ENABLED: bool = os.getenv("API_KEY_AUTH_ENABLED", "true").lower() == "true"
BYPASS_PATHS: set[str] = {
    path.strip()
    for path in os.getenv(
        "BYPASS_PATHS",
        "/health,/docs,/openapi.json,/redoc,/favicon.ico",
    ).split(",")
    if path.strip()
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of the raw API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _extract_key(request: Request) -> Optional[str]:
    """
    Extract the raw API key from the request.

    Priority:
        1. ``X-API-Key`` header
        2. ``api_key`` query parameter
    """
    # Header takes precedence
    header_key = request.headers.get("x-api-key")
    if header_key:
        return header_key.strip()

    # Fallback to query parameter
    query_key = request.query_params.get("api_key")
    if query_key:
        return query_key.strip()

    return None


def _build_error_response(
    status_code: int,
    error: str,
    detail: str,
) -> JSONResponse:
    """Build a structured error JSON response."""
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "detail": detail},
    )


# ---------------------------------------------------------------------------
# API Key database lookup
# ---------------------------------------------------------------------------


def _lookup_api_key(key_hash: str, db: Session) -> Optional[dict]:
    """
    Look up an API key by its SHA-256 hash.

    Returns a dict with key metadata or ``None`` if not found / inactive / expired.
    """
    try:
        from app.models import APIKey as APIKeyModel  # SQLAlchemy model

        stmt = select(APIKeyModel).where(APIKeyModel.key_hash == key_hash)
        result = db.scalar(stmt)

        if result is None:
            return None

        # Check active flag
        if not result.is_active:
            logger.warning("Inactive API key used: name=%s", result.name)
            return None

        # Check expiration
        if result.expires_at is not None:
            now = datetime.now(timezone.utc)
            if result.expires_at < now:
                logger.warning("Expired API key used: name=%s, expired=%s", result.name, result.expires_at)
                return None

        return {
            "id": result.id,
            "name": result.name,
            "rate_limit": result.rate_limit,
        }
    except Exception as exc:
        logger.error("Error looking up API key: %s", exc)
        return None


def _update_last_used(key_hash: str, db: Session) -> None:
    """Update the ``last_used_at`` timestamp for the matched key."""
    try:
        from app.models import APIKey as APIKeyModel

        stmt = select(APIKeyModel).where(APIKeyModel.key_hash == key_hash)
        record = db.scalar(stmt)
        if record is not None:
            record.last_used_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        logger.error("Error updating last_used_at: %s", exc)
        db.rollback()


# ---------------------------------------------------------------------------
# Per-key in-memory rate limiter (simple counter, reset each minute)
# ---------------------------------------------------------------------------

_per_key_counts: dict[str, tuple[int, float]] = {}  # key_hash -> (count, window_start)


def _check_per_key_rate_limit(key_hash: str, rate_limit: int) -> bool:
    """
    Enforce a per-key rate limit.

    Returns ``True`` if the request is allowed, ``False`` if the limit is exceeded.
    Uses a simple fixed-window counter.
    """
    import time

    now = time.time()
    window_start = now - (now % 60)  # Align to minute boundary

    if key_hash in _per_key_counts:
        count, ws = _per_key_counts[key_hash]
        if ws == window_start:
            if count >= rate_limit:
                return False
            _per_key_counts[key_hash] = (count + 1, ws)
            return True

    # New window
    _per_key_counts[key_hash] = (1, window_start)
    return True


# ---------------------------------------------------------------------------
# Middleware class
# ---------------------------------------------------------------------------


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that enforces API key authentication.

    Auth is skipped for:
        - Paths listed in ``BYPASS_PATHS``
        - Requests with a valid ``X-Admin-Token`` header
        - All requests if ``API_KEY_AUTH_ENABLED`` is ``false``
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # ------------------------------------------------------------------
        # 1. Skip auth for bypass paths
        # ------------------------------------------------------------------
        path = request.url.path
        if path in BYPASS_PATHS or not API_KEY_AUTH_ENABLED:
            return await call_next(request)

        # ------------------------------------------------------------------
        # 2. Admin bypass
        # ------------------------------------------------------------------
        admin_token = request.headers.get("x-admin-token")
        if ADMIN_TOKEN and admin_token == ADMIN_TOKEN:
            request.state.api_key = {"id": None, "name": "admin", "is_admin": True}
            logger.debug("Admin bypass for path=%s", path)
            return await call_next(request)

        # ------------------------------------------------------------------
        # 3. Extract API key
        # ------------------------------------------------------------------
        raw_key = _extract_key(request)
        if raw_key is None:
            return _build_error_response(
                status_code=status.HTTP_401_UNAUTHORIZED,
                error="Unauthorized",
                detail="API key is required. Provide it via 'X-API-Key' header or 'api_key' query parameter.",
            )

        # ------------------------------------------------------------------
        # 4. Hash & look up
        # ------------------------------------------------------------------
        key_hash = _hash_key(raw_key)

        db: Session = SessionLocal()
        try:
            key_info = _lookup_api_key(key_hash, db)
        finally:
            db.close()

        if key_info is None:
            return _build_error_response(
                status_code=status.HTTP_403_FORBIDDEN,
                error="Forbidden",
                detail="Invalid, inactive, or expired API key.",
            )

        # ------------------------------------------------------------------
        # 5. Per-key rate limit
        # ------------------------------------------------------------------
        key_rate_limit = key_info.get("rate_limit", 100)
        if not _check_per_key_rate_limit(key_hash, key_rate_limit):
            return _build_error_response(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                error="Too Many Requests",
                detail=f"Per-key rate limit exceeded ({key_rate_limit} requests/minute).",
            )

        # ------------------------------------------------------------------
        # 6. Update last_used_at in background (best-effort)
        # ------------------------------------------------------------------
        try:
            db = SessionLocal()
            try:
                _update_last_used(key_hash, db)
            finally:
                db.close()
        except Exception:
            pass  # Non-critical; don't block the request

        # ------------------------------------------------------------------
        # 7. Attach key info to request state
        # ------------------------------------------------------------------
        request.state.api_key = {
            "id": key_info["id"],
            "name": key_info["name"],
            "rate_limit": key_rate_limit,
            "is_admin": False,
        }

        return await call_next(request)
