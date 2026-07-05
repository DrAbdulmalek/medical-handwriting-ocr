"""
Locust load test file for Medical Handwriting OCR API.

Simulates realistic user behaviour against a running instance of the API.
Each virtual user cycles through a weighted mix of read-only and
write operations.

Run (against localhost):
    locust -f tests/locustfile.py --host=http://localhost:8000

Run (headless, 50 users, 10 hatch rate):
    locust -f tests/locustfile.py --host=http://localhost:8000 \
          --headless -u 50 -r 10 -t 60s --html=load_report.html

Run (with custom spawn rate and duration):
    locust -f tests/locustfile.py --host=http://localhost:8000 \
          --headless -u 100 -r 20 -t 300s \
          --only-summary --csv=load_results

Web UI:
    locust -f tests/locustfile.py --host=http://localhost:8000
    # Open http://localhost:8089
"""

from __future__ import annotations

import io
import random
import struct
import zlib
from typing import List

from locust import HttpUser, task, between, events


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _make_png(width: int = 50, height: int = 50) -> bytes:
    """Generate a minimal valid PNG image in memory."""

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw_rows = (b"\x00" * width) * height
    idat = _chunk(b"IDAT", zlib.compress(raw_rows))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _headers() -> dict:
    """Return common request headers."""
    hdrs = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if API_KEY:
        hdrs["X-API-Key"] = API_KEY
    return hdrs


# ─────────────────────────────────────────────────────────────
# Configuration constants
# ─────────────────────────────────────────────────────────────

# Test image data (small 50×50 PNG embedded inline so no file I/O)
_SMALL_PNG_BYTES: bytes = _make_png(50, 50)

# API Key for authenticated endpoints (set via --env API_KEY=...)
# If empty, the API must have API_KEY_AUTH_ENABLED=false
API_KEY: str = ""  # Override with: locust ... --env API_KEY=your-key

# Sample medical terms for suggestion queries (Arabic + Latin)
_MEDICAL_TERMS: List[str] = [
    "Ostecb(astoma",
    "Chondrosarcoma",
    "FOGMACIN",
    "GENTAMICIN",
    "Osteomyelitis",
    "الفقرات القطنية",
    "الفقارة الصدرية",
    "ORIF",
    "AVN",
    "CT Scan",
    "MRI",
    "Fracture",
    "Osteoblastoma",
    "Osteoporosis",
    "العجزية",
]


# ─────────────────────────────────────────────────────────────
# Locust User
# ─────────────────────────────────────────────────────────────


class OCRUser(HttpUser):
    """
    Virtual user that simulates typical Medical Handwriting OCR API usage.

    Task weights approximate a realistic traffic mix:
      - Health checks are lightweight and frequent
      - Uploads are the heaviest operation (OCR processing)
      - Pending-region fetches and suggestion lookups are read-heavy
    """

    # Wait 1–3 seconds between tasks (simulates human think-time)
    wait_time = between(1, 3)

    # ── Read-only tasks ──────────────────────────────────

    @task(1)
    def health_check(self):
        """GET /health — lightweight liveness probe."""
        with self.client.get(
            "/health",
            headers=_headers(),
            name="/health",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 503):
                response.success()
            else:
                response.failure(
                    f"Unexpected status: {response.status_code}"
                )

    @task(2)
    def get_pending(self):
        """GET /api/pending — fetch regions needing human review."""
        with self.client.get(
            "/api/pending",
            headers=_headers(),
            params={"limit": "20"},
            name="/api/pending",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 401:
                response.success()  # Auth may be enabled
            else:
                response.failure(
                    f"Unexpected status: {response.status_code}"
                )

    @task(2)
    def get_suggestions(self):
        """GET /suggestions/ — query the suggestion engine."""
        term = random.choice(_MEDICAL_TERMS)
        with self.client.get(
            "/suggestions/",
            headers=_headers(),
            params={
                "text": term,
                "is_medical": "true",
                "context_before": "Patient presents with",
            },
            name="/suggestions/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                try:
                    body = response.json()
                    if "suggestions" in body:
                        response.success()
                    else:
                        response.failure("Missing 'suggestions' key in response")
                except Exception:
                    response.failure("Invalid JSON response")
            elif response.status_code == 401:
                response.success()
            else:
                response.failure(
                    f"Unexpected status: {response.status_code}"
                )

    # ── Write tasks ───────────────────────────────────────

    @task(3)
    def upload_document(self):
        """
        POST /api/upload — upload a document image for OCR processing.

        This is the most resource-intensive operation (image decoding,
        OCR inference, crop extraction, DB writes, MinIO upload).
        """
        filename = f"locust_upload_{self.user_id}_{random.randint(1000, 9999)}.png"
        with self.client.post(
            "/api/upload",
            headers=_headers(),
            files={"file": (filename, io.BytesIO(_SMALL_PNG_BYTES), "image/png")},
            data={"user_id": f"locust-user-{self.user_id}"},
            name="/api/upload",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                try:
                    body = response.json()
                    if "document_id" in body and "regions" in body:
                        response.success()
                    else:
                        response.failure("Missing keys in upload response")
                except Exception:
                    response.failure("Invalid JSON in upload response")
            elif response.status_code == 400:
                # Image decode failure — acceptable in load test
                response.success()
            elif response.status_code == 401:
                response.success()
            elif response.status_code == 413:
                response.success()
            else:
                response.failure(
                    f"Upload failed: {response.status_code} "
                    f"{response.text[:200]}"
                )

    @task(1)
    def submit_correction(self):
        """
        POST /api/correct — submit a correction for an OCR region.

        This is a lower-frequency operation but important for
        measuring DB write performance under load.
        """
        import uuid

        payload = {
            "region_id": str(uuid.uuid4()),
            "corrected_text": random.choice(_MEDICAL_TERMS),
            "user_id": f"locust-user-{self.user_id}",
        }
        with self.client.post(
            "/api/correct",
            headers=_headers(),
            json=payload,
            name="/api/correct",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 404:
                # Region not found — expected for random UUID
                response.success()
            elif response.status_code == 401:
                response.success()
            else:
                response.failure(
                    f"Correction failed: {response.status_code}"
                )

    @task(1)
    def get_root(self):
        """GET / — API root endpoint for discovering available endpoints."""
        with self.client.get(
            "/",
            headers=_headers(),
            name="/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(
                    f"Unexpected status: {response.status_code}"
                )


# ─────────────────────────────────────────────────────────────
# Event handlers for custom stats / reporting
# ─────────────────────────────────────────────────────────────


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """Log individual request failures for post-run analysis."""
    if exception:
        # Locust already tracks failure stats; this hook can be
        # extended to push to external monitoring (e.g. Datadog).
        pass


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when the load test starts."""
    print(f"\n{'='*60}")
    print(f"  Medical Handwriting OCR — Locust Load Test")
    print(f"  Target: {environment.host}")
    print(f"  API Key: {'configured' if API_KEY else 'not set'}")
    print(f"{'='*60}\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when the load test ends — print summary."""
    stats = environment.runner.stats
    print(f"\n{'='*60}")
    print(f"  Load Test Complete")
    print(f"{'='*60}")
    print(f"  Total Requests:     {stats.total.num_requests}")
    print(f"  Total Failures:     {stats.total.num_failures}")
    print(f"  Failure Rate:      {stats.total.fail_ratio:.2%}")
    print(f"  Avg Response Time:  {stats.total.avg_response_time:.0f}ms")
    print(f"  Min Response Time:  {stats.total.min_response_time:.0f}ms")
    print(f"  Max Response Time:  {stats.total.max_response_time:.0f}ms")
    print(f"  RPS:                {stats.total.current_rps:.1f}")
    print(f"{'='*60}\n")
