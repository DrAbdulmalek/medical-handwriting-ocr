"""
Security headers middleware for Medical Handwriting OCR API.

Adds a comprehensive set of HTTP security headers to every response to
protect against common web vulnerabilities including XSS, clickjacking,
MIME sniffing, and information leakage.

Headers added:
    X-Content-Type-Options  – Prevent MIME type sniffing
    X-Frame-Options         – Prevent clickjacking (configurable)
    X-XSS-Protection        – Enable browser XSS filter (legacy browsers)
    Referrer-Policy         – Control referrer information leakage
    Content-Security-Policy  – Restrict resource origins (configurable)
    Strict-Transport-Security – Enforce HTTPS in production
    Permissions-Policy       – Disable unneeded browser features
    Cache-Control            – Prevent caching of API responses

Configuration (via environment variables):
    ENVIRONMENT             – "production" enables HSTS (default: "development")
    SECURITY_FRAME_OPTIONS  – DENY, SAMEORIGIN, or ALLOW-FROM (default: "DENY")
    SECURITY_CSP_POLICY     – Full CSP directive string
                             (default: "default-src 'self'; script-src 'none';
                              style-src 'none'; img-src 'self' data:; font-src 'self'")
    SECURITY_HSTS_MAX_AGE   – HSTS max-age in seconds (default: 31536000)
"""

from __future__ import annotations

import logging
import os
from typing import List

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

SECURITY_FRAME_OPTIONS: str = os.getenv("SECURITY_FRAME_OPTIONS", "DENY")

SECURITY_CSP_POLICY: str = os.getenv(
    "SECURITY_CSP_POLICY",
    "default-src 'self'; script-src 'none'; style-src 'none'; "
    "img-src 'self' data:; font-src 'self'",
)

SECURITY_HSTS_MAX_AGE: int = int(os.getenv("SECURITY_HSTS_MAX_AGE", "31536000"))

# ---------------------------------------------------------------------------
# Valid frame-options values (used for validation at startup)
# ---------------------------------------------------------------------------

_VALID_FRAME_OPTIONS: frozenset[str] = frozenset({"DENY", "SAMEORIGIN", "ALLOW-FROM"})

# ---------------------------------------------------------------------------
# Paths where Cache-Control should NOT be overridden (e.g. static assets)
# ---------------------------------------------------------------------------

_CACHE_EXEMPT_PREFIXES: List[str] = [
    "/docs",
    "/redoc",
    "/openapi.json",
    "/static",
]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that injects security headers into every HTTP
    response.

    The middleware is designed to be **non-destructive**: it will not
    overwrite a security header that was already set by a downstream
    route handler.  This allows individual endpoints to tighten or relax
    a specific header when required.

    HSTS (Strict-Transport-Security) is only added when ``ENVIRONMENT``
    is set to ``"production"`` so that local development over plain
    HTTP is not broken.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response: Response = await call_next(request)
        path = request.url.path

        # --- X-Content-Type-Options: nosniff ---
        self._set_header(response, "X-Content-Type-Options", "nosniff")

        # --- X-Frame-Options ---
        self._set_header(response, "X-Frame-Options", SECURITY_FRAME_OPTIONS)

        # --- X-XSS-Protection (legacy browser XSS auditor) ---
        self._set_header(response, "X-XSS-Protection", "1; mode=block")

        # --- Referrer-Policy ---
        self._set_header(
            response,
            "Referrer-Policy",
            "strict-origin-when-cross-origin",
        )

        # --- Content-Security-Policy ---
        self._set_header(response, "Content-Security-Policy", SECURITY_CSP_POLICY)

        # --- Strict-Transport-Security (production only) ---
        if ENVIRONMENT == "production":
            hsts_value = (
                f"max-age={SECURITY_HSTS_MAX_AGE}; includeSubDomains"
            )
            self._set_header(response, "Strict-Transport-Security", hsts_value)

        # --- Permissions-Policy ---
        self._set_header(
            response,
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )

        # --- Cache-Control: no-store (API responses) ---
        # Skip paths that serve static/documentation assets which benefit
        # from caching.
        if not any(path.startswith(prefix) for prefix in _CACHE_EXEMPT_PREFIXES):
            self._set_header(response, "Cache-Control", "no-store")

        return response

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _set_header(response: Response, name: str, value: str) -> None:
        """
        Set a response header **only if it is not already present**.

        This preserves any header values explicitly set by route handlers.
        """
        if name not in response.headers:
            response.headers[name] = value


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_frame_options() -> None:
    """Log a warning if ``SECURITY_FRAME_OPTIONS`` contains an invalid value."""
    if SECURITY_FRAME_OPTIONS not in _VALID_FRAME_OPTIONS:
        logger.warning(
            "Invalid SECURITY_FRAME_OPTIONS value: %r (expected one of %s). "
            "Header will still be set but may be ignored by browsers.",
            SECURITY_FRAME_OPTIONS,
            sorted(_VALID_FRAME_OPTIONS),
        )


def _validate_csp() -> None:
    """Log a warning if the CSP policy appears dangerously permissive."""
    if "unsafe-inline" in SECURITY_CSP_POLICY or "*" in SECURITY_CSP_POLICY:
        logger.warning(
            "SECURITY_CSP_POLICY contains 'unsafe-inline' or '*' which may "
            "weaken XSS protection: %s",
            SECURITY_CSP_POLICY,
        )


# ---------------------------------------------------------------------------
# Setup function – call once in main.py
# ---------------------------------------------------------------------------


def setup_security_headers(app: FastAPI) -> None:
    """
    Register the ``SecurityHeadersMiddleware`` on a FastAPI application.

    Performs lightweight startup validation of configuration values and
    logs the effective settings at INFO level.

    Usage (in main.py)::

        from app.middleware.security_headers import setup_security_headers
        setup_security_headers(app)

    .. note::
        For defense-in-depth this middleware should be added **last** (i.e.
        closest to the client) so that its headers are applied to every
        response regardless of other middleware behaviour.
    """
    _validate_frame_options()
    _validate_csp()

    app.add_middleware(SecurityHeadersMiddleware)

    logger.info(
        "Security headers middleware registered (frame_options=%s, hsts=%s, environment=%s)",
        SECURITY_FRAME_OPTIONS,
        "enabled" if ENVIRONMENT == "production" else "disabled",
        ENVIRONMENT,
    )
