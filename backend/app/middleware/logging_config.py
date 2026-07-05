"""
Structured JSON logging middleware for FastAPI.

Features:
  - JSON-formatted log lines (machine-parseable)
  - Automatic request-id / correlation-id tracking
  - Logs method, path, status code, response time, client IP
  - Includes request-id in response headers (X-Request-ID)
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Environment label – useful when multiple services ship to the same aggregator
# ---------------------------------------------------------------------------
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON object."""

    # Fields that already exist on LogRecord and should be passed through
    RESERVED = {"message", "asctime", "levelname", "name", "filename", "lineno"}

    def format(self, record: logging.LogRecord) -> str:
        import json

        log_entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "environment": ENVIRONMENT,
            "message": record.getMessage(),
        }

        # Merge in any extra dict passed via `extra={"fields": {…}}`
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for k, v in fields.items():
                if k not in self.RESERVED:
                    log_entry[k] = v

        # Include exception info when present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Logging setup helper
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> None:
    """Configure the root (and uvicorn) logger with the JSON formatter."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.handlers.clear()
    root.addHandler(handler)

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "hpack", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Request-Id helpers
# ---------------------------------------------------------------------------

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"


def _generate_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class LoggingMiddleware(BaseHTTPMiddleware):
    """
    For every HTTP request/response pair this middleware:
      1. Assigns (or reuses) a request-id and correlation-id
      2. Times the request
      3. Emits a single structured JSON log line
      4. Injects the request-id into the response header
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # --- correlation / request IDs ---
        request_id = request.headers.get(REQUEST_ID_HEADER) or _generate_id()
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or _generate_id()

        # Store on request state so route handlers can access them
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        # --- timing ---
        start = time.perf_counter()

        # --- execute ---
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            status_code = 500
            logging.getLogger("ocr.app").error(
                "Unhandled exception",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "correlation_id": correlation_id,
                        "method": request.method,
                        "path": request.url.path,
                        "exception": str(exc),
                    }
                },
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000

        # --- structured log ---
        logger = logging.getLogger("ocr.access")
        log_fields = {
            "request_id": request_id,
            "correlation_id": correlation_id,
            "method": request.method,
            "path": request.url.path,
            "query_string": request.url.query if request.url.query else None,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
        }

        if status_code >= 500:
            logger.error("request completed", extra={"fields": log_fields})
        elif status_code >= 400:
            logger.warning("request completed", extra={"fields": log_fields})
        else:
            logger.info("request completed", extra={"fields": log_fields})

        # --- inject headers into response ---
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[CORRELATION_ID_HEADER] = correlation_id

        return response


# ---------------------------------------------------------------------------
# Convenience: attach middleware to a FastAPI app
# ---------------------------------------------------------------------------

def add_logging_middleware(app) -> None:  # noqa: ANN001
    """
    Register the LoggingMiddleware and configure root logging.

    Usage (in main.py):
        from backend.app.middleware.logging_config import add_logging_middleware
        add_logging_middleware(app)
    """
    setup_logging()
    app.add_middleware(LoggingMiddleware)
