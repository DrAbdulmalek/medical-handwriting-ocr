"""
Rate limiting middleware for Medical Handwriting OCR API.

Uses SlowAPI (a FastAPI extension built on limits) with an optional Redis
backend for distributed rate limiting. Falls back to in-memory storage
when Redis is unavailable.

Features:
- Per-IP default rate limit (100 req/min)
- Stricter limits for expensive endpoints (upload: 20/min, corrections: 60/min)
- Custom 429 exception handler with structured JSON response
- Standard rate-limit headers on every response (X-RateLimit-*)

Configuration (via environment variables):
    REDIS_URL          – Redis connection string (default: redis://localhost:6379/2)
    RATE_LIMIT_DEFAULT – Global default requests per minute (default: 100)
    RATE_LIMIT_UPLOAD  – Upload endpoint requests per minute (default: 20)
    RATE_LIMIT_CORRECT – Correction endpoint requests per minute (default: 60)
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.stores.redis import RedisStore
from slowapi.stores.memory import MemoryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/2")
RATE_LIMIT_DEFAULT: str = os.getenv("RATE_LIMIT_DEFAULT", "100/minute")
RATE_LIMIT_UPLOAD: str = os.getenv("RATE_LIMIT_UPLOAD", "20/minute")
RATE_LIMIT_CORRECT: str = os.getenv("RATE_LIMIT_CORRECT", "60/minute")

# ---------------------------------------------------------------------------
# Backend store (Redis with automatic in-memory fallback)
# ---------------------------------------------------------------------------


def _create_store() -> MemoryStore | RedisStore:
    """Instantiate a Redis store; fall back to in-memory on connection error."""
    try:
        import redis as redis_lib

        client = redis_lib.from_url(REDIS_URL, socket_connect_timeout=2)
        client.ping()  # Validate connectivity
        logger.info("Rate limiter using Redis backend at %s", REDIS_URL)
        return RedisStore(client=client)
    except Exception as exc:  # pragma: no cover – graceful degradation
        logger.warning(
            "Redis unavailable (%s); falling back to in-memory rate limiting",
            exc,
        )
        return MemoryStore()


# ---------------------------------------------------------------------------
# Limiter instance (singleton – attached to app.state.limiter in main.py)
# ---------------------------------------------------------------------------

limiter: Limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[RATE_LIMIT_DEFAULT],
    store=_create_store(),
    headers_enabled=True,  # Include X-RateLimit-* response headers
    strategy="fixed-window-elastic-expiry",
)

# Convenience references used in route decorators
LIMIT_UPLOAD: str = RATE_LIMIT_UPLOAD
LIMIT_CORRECT: str = RATE_LIMIT_CORRECT

# ---------------------------------------------------------------------------
# Custom exception handler – returns structured JSON instead of plain text
# ---------------------------------------------------------------------------


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Return a structured 429 JSON response when a client exceeds its rate limit.

    Includes:
    - ``error``: Human-readable message
    - ``detail``: Technical detail from SlowAPI
    - ``retry_after``: Seconds until the window resets
    """
    retry_after = int(exc.detail.split("Retry after ")[-1]) if "Retry after" in str(exc.detail) else 60

    headers = {
        "X-RateLimit-Limit": str(exc.limit),
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int(time.time()) + retry_after),
        "Retry-After": str(retry_after),
    }

    # SlowAPI may have already set these on the request state
    if hasattr(request.state, "remaining") and request.state.remaining is not None:
        headers["X-RateLimit-Remaining"] = str(request.state.remaining)
    if hasattr(request.state, "reset") and request.state.reset is not None:
        headers["X-RateLimit-Reset"] = str(request.state.reset)

    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error": "Too Many Requests",
            "detail": f"Rate limit exceeded. {exc.detail}",
            "retry_after": retry_after,
        },
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Helper: attach rate-limit headers to every response via middleware
# ---------------------------------------------------------------------------


class RateLimitHeaders:
    """
    ASGI middleware that ensures standard ``X-RateLimit-*`` headers are present
    on every response, even when the request was *not* rate-limited.

    Headers added:
        X-RateLimit-Limit     – Limit for the current window
        X-RateLimit-Remaining – Remaining requests in the window
        X-RateLimit-Reset     – Unix timestamp when the window resets
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Wrap send to inject headers if not already present
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                existing_headers = {
                    k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                    for k, v in message.get("headers", [])
                }
                rate_headers = {
                    "x-ratelimit-limit": existing_headers.get("x-ratelimit-limit", RATE_LIMIT_DEFAULT.split("/")[0]),
                    "x-ratelimit-remaining": existing_headers.get("x-ratelimit-remaining", "-"),
                    "x-ratelimit-reset": existing_headers.get("x-ratelimit-reset", str(int(time.time()) + 60)),
                }
                for key, value in rate_headers.items():
                    if key not in existing_headers:
                        message["headers"].append(
                            (key.encode() if isinstance(key, str) else key,
                             value.encode() if isinstance(value, str) else value)
                        )
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------------------
# Registration helper – call once in main.py
# ---------------------------------------------------------------------------


def register_rate_limiter(app) -> None:
    """
    Wire the rate limiter into a FastAPI application.

    - Attaches the limiter to ``app.state.limiter``
    - Registers the custom 429 exception handler
    - Adds the ``RateLimitHeaders`` middleware
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(RateLimitHeaders)
    logger.info("Rate limiter registered (default=%s, upload=%s, correct=%s)", RATE_LIMIT_DEFAULT, RATE_LIMIT_UPLOAD, RATE_LIMIT_CORRECT)
