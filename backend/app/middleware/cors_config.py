"""
CORS (Cross-Origin Resource Sharing) configuration for Medical Handwriting OCR API.

Origins are loaded from the ``ALLOWED_ORIGINS`` environment variable as a
comma-separated list. When unset, sensible development defaults are used.

Configuration (via environment variables):
    ALLOWED_ORIGINS – Comma-separated list of allowed origins.
                      Default: "http://localhost:3000,http://localhost:8080"

Security defaults:
    - allow_credentials: True   (needed for cookie/auth header support)
    - allow_methods:     GET, POST, PUT, DELETE, OPTIONS
    - allow_headers:     Content-Type, Authorization, X-API-Key, X-Admin-Token
    - max_age:           600 seconds (10-minute preflight cache)
"""

from __future__ import annotations

import logging
import os
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Allowed HTTP methods – restricted to what the application actually uses
_ALLOWED_METHODS: List[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]

# Allowed request headers – only those required by the API
_ALLOWED_HEADERS: List[str] = [
    "Content-Type",
    "Authorization",
    "X-API-Key",
    "X-Admin-Token",
    "Accept",
    "Origin",
]

# Preflight cache duration in seconds
_MAX_AGE: int = 600

# Default origins for local development
_DEFAULT_ORIGINS: List[str] = [
    "http://localhost:3000",
    "http://localhost:8080",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_origins(env_value: str) -> List[str]:
    """
    Parse the ALLOWED_ORIGINS environment variable.

    Supports:
        - Comma-separated list of origins
        - Single "*" to allow all (discouraged in production)
    """
    origins = [origin.strip() for origin in env_value.split(",") if origin.strip()]

    if not origins:
        logger.warning(
            "ALLOWED_ORIGINS is empty; falling back to defaults: %s",
            _DEFAULT_ORIGINS,
        )
        return _DEFAULT_ORIGINS

    return origins


def get_allowed_origins() -> List[str]:
    """Return the configured list of allowed CORS origins."""
    raw = os.getenv("ALLOWED_ORIGINS", "")
    return _parse_origins(raw) if raw else list(_DEFAULT_ORIGINS)


# ---------------------------------------------------------------------------
# Setup function – call once in main.py
# ---------------------------------------------------------------------------


def setup_cors(app: FastAPI) -> None:
    """
    Configure CORS middleware on the given FastAPI application.

    This replaces any manually-added CORSMiddleware in ``main.py``.
    Ensure this is called *before* other middleware that may depend on
    CORS headers being present.
    """
    origins = get_allowed_origins()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=_ALLOWED_METHODS,
        allow_headers=_ALLOWED_HEADERS,
        max_age=_MAX_AGE,
    )

    if "*" in origins:
        logger.warning(
            "CORS configured with wildcard origin (*) – do NOT use in production!"
        )
    else:
        logger.info("CORS configured with %d allowed origin(s): %s", len(origins), origins)
