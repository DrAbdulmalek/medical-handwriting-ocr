"""
Prometheus metrics exporter for the Medical Handwriting OCR FastAPI application.

Exposes a ``/metrics`` endpoint and provides a Starlette middleware that
automatically collects per-request HTTP metrics.

Usage in ``main.py``::

    from backend.app.metrics import metrics_middleware, metrics_app

    app.mount("/metrics", metrics_app)

    # or, to add automatic request instrumentation:
    app.add_middleware(MetricsMiddleware)
"""

from __future__ import annotations

import os
import time
from typing import Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

# ---------------------------------------------------------------------------
# Environment label value
# ---------------------------------------------------------------------------
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
REGISTRY = CollectorRegistry()

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

# Total HTTP requests handled by the OCR service
ocr_requests_total = Counter(
    "ocr_requests_total",
    "Total number of HTTP requests received.",
    labelnames=["method", "endpoint", "status", "environment"],
    registry=REGISTRY,
)

# End-to-end request duration (including queueing, but measured at the gateway)
ocr_request_duration_seconds = Histogram(
    "ocr_request_duration_seconds",
    "End-to-end request duration in seconds.",
    labelnames=["method", "endpoint", "environment"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60],
    registry=REGISTRY,
)

# Time spent *inside* the OCR model (inference only)
ocr_processing_duration_seconds = Histogram(
    "ocr_processing_duration_seconds",
    "Duration of OCR inference (model forward-pass) in seconds.",
    labelnames=["environment"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20],
    registry=REGISTRY,
)

# Current depth of the Celery task queue
ocr_queue_size = Gauge(
    "ocr_queue_size",
    "Number of pending OCR tasks in the Celery queue.",
    labelnames=["environment"],
    registry=REGISTRY,
)

# Number of celery workers currently processing an OCR task
ocr_active_workers = Gauge(
    "ocr_active_workers",
    "Number of active Celery workers processing OCR tasks.",
    labelnames=["environment"],
    registry=REGISTRY,
)

# Latest confidence score reported by the model (set after each inference)
ocr_confidence_score = Gauge(
    "ocr_confidence_score",
    "OCR confidence score for the last processed document.",
    labelnames=["script_class", "environment"],
    registry=REGISTRY,
)

# Histogram for confidence score distribution over time
ocr_confidence_distribution = Histogram(
    "ocr_confidence_distribution",
    "Distribution of OCR confidence scores.",
    labelnames=["environment"],
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0],
    registry=REGISTRY,
)

# Total manual corrections applied by users (post-OCR edits)
ocr_corrections_total = Counter(
    "ocr_corrections_total",
    "Total number of manual corrections applied to OCR output.",
    labelnames=["script_class", "environment"],
    registry=REGISTRY,
)

# Medical term detection rate gauge
ocr_medical_term_detection_rate = Gauge(
    "ocr_medical_term_detection_rate",
    "Fraction of detected medical terms that matched a known dictionary entry.",
    labelnames=["environment"],
    registry=REGISTRY,
)

# Training metrics – set periodically by the training pipeline
ocr_training_loss = Gauge(
    "ocr_training_loss",
    "Current training loss value.",
    labelnames=["model_version", "environment"],
    registry=REGISTRY,
)

ocr_validation_loss = Gauge(
    "ocr_validation_loss",
    "Current validation loss value.",
    labelnames=["model_version", "environment"],
    registry=REGISTRY,
)

ocr_training_accuracy = Gauge(
    "ocr_training_accuracy",
    "Current training accuracy.",
    labelnames=["model_version", "environment"],
    registry=REGISTRY,
)

ocr_validation_accuracy = Gauge(
    "ocr_validation_accuracy",
    "Current validation accuracy.",
    labelnames=["model_version", "environment"],
    registry=REGISTRY,
)

# Info gauge for the currently deployed model version
ocr_model_version_info = Gauge(
    "ocr_model_version_info",
    "Metadata about the currently deployed OCR model. Value is always 1.",
    labelnames=["version", "environment"],
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# /metrics ASGI "app"
# ---------------------------------------------------------------------------

async def _metrics_handler(request: Request) -> Response:  # noqa: ARG001
    """Small ASGI handler that returns the Prometheus metrics dump."""
    body = generate_latest(REGISTRY)
    return PlainTextResponse(content=body, media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# Middleware – automatic request instrumentation
# ---------------------------------------------------------------------------

class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that records request count and latency for every
    HTTP call passing through the application.

    The ``endpoint`` label is derived from the *route path* (e.g.
    ``/api/v1/ocr``) so that templated paths collapse nicely.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        method = request.method

        # Best-effort route template; fall back to raw path
        path = getattr(request, "path", "")
        endpoint = getattr(request.app, "root_path", "") + path

        # Try to resolve the matched route name (Starlette ≥0.20)
        route = getattr(request, "route", None)
        if route is not None and hasattr(route, "path"):
            endpoint = route.path

        start = time.perf_counter()

        try:
            response: Response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            status = "500"
            raise
        finally:
            duration = time.perf_counter() - start
            ocr_requests_total.labels(
                method=method, endpoint=endpoint, status=status, environment=ENVIRONMENT
            ).inc()
            ocr_request_duration_seconds.labels(
                method=method, endpoint=endpoint, environment=ENVIRONMENT
            ).observe(duration)

        return response


# ---------------------------------------------------------------------------
# Helpers – expose the middleware factory for easy integration
# ---------------------------------------------------------------------------

def get_metrics_middleware() -> type[MetricsMiddleware]:
    """Return the middleware class (for ``app.add_middleware``)."""
    return MetricsMiddleware
