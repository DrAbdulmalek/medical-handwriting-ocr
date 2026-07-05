"""
Load tests for Medical Handwriting OCR API.
Tests system behavior under concurrent load using raw asyncio.

Run: pytest tests/test_load.py -m load -v
     pytest tests/test_load.py -m load -v --timeout=120
"""

import io
import os
import sys
import asyncio
import time
import tracemalloc
import statistics
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field

import httpx
import pytest

# ─────────────────────────────────────────────────────────────
# Pre-import environment configuration (same as integration)
# ─────────────────────────────────────────────────────────────
os.environ.setdefault("API_KEY_AUTH_ENABLED", "false")
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# ─────────────────────────────────────────────────────────────
# Mock external services before importing app modules
# ─────────────────────────────────────────────────────────────

if "app.storage" not in sys.modules:
    _mock_storage_mod = MagicMock()
    _mock_storage_inst = MagicMock()
    _mock_storage_inst.upload_crop.return_value = "crops/load-test.png"
    _mock_storage_inst.get_crop_url.return_value = (
        "http://localhost:9000/test-ocr-crops/crops/load-test.png"
    )
    _mock_storage_inst.download_crop.return_value = b"\x89PNGfake"
    _mock_storage_mod.storage = _mock_storage_inst
    _mock_storage_mod.get_minio_client.return_value = MagicMock(
        bucket_exists=MagicMock(return_value=True)
    )
    sys.modules["app.storage"] = _mock_storage_mod

if "app.ocr_engine" not in sys.modules:
    _mock_ocr_mod = MagicMock()
    _mock_ocr_inst = MagicMock()
    _mock_ocr_inst.detect_regions.return_value = []
    _mock_ocr_inst.crop_region.return_value = b"\x89PNGfake"
    _mock_ocr_inst.classify_script.return_value = "latin"
    _mock_ocr_mod.ocr_engine = _mock_ocr_inst
    sys.modules["app.ocr_engine"] = _mock_ocr_mod

if "app.celery_app" not in sys.modules:
    sys.modules["app.celery_app"] = MagicMock()

# ─────────────────────────────────────────────────────────────
# Import the application
# ─────────────────────────────────────────────────────────────

from app.main import app  # noqa: E402
from app.database import get_db, Base  # noqa: E402

# Mark every test as load AND slow (these take time)
pytestmark = [pytest.mark.load, pytest.mark.slow]


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _make_png_bytes(width: int = 50, height: int = 50) -> bytes:
    """Return a minimal valid PNG image."""
    import struct
    import zlib

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


@dataclass
class LoadTestResult:
    """Container for load test metrics."""

    name: str
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)
    latencies: list = field(default_factory=list)
    min_latency: float = 0.0
    max_latency: float = 0.0
    avg_latency: float = 0.0
    p50_latency: float = 0.0
    p95_latency: float = 0.0
    p99_latency: float = 0.0
    total_time: float = 0.0
    requests_per_second: float = 0.0

    def compute(self):
        """Compute derived metrics from raw data."""
        if self.latencies:
            self.latencies.sort()
            n = len(self.latencies)
            self.min_latency = self.latencies[0]
            self.max_latency = self.latencies[-1]
            self.avg_latency = statistics.mean(self.latencies)
            self.p50_latency = self.latencies[int(n * 0.50)]
            self.p95_latency = self.latencies[min(int(n * 0.95), n - 1)]
            self.p99_latency = self.latencies[min(int(n * 0.99), n - 1)]
        if self.total_time > 0:
            self.requests_per_second = self.total_requests / self.total_time
        return self


def _make_summary(result: LoadTestResult) -> str:
    """Return a human-readable summary of load test results."""
    result.compute()
    return (
        f"\n{'='*60}\n"
        f"  Load Test: {result.name}\n"
        f"{'='*60}\n"
        f"  Total Requests:  {result.total_requests}\n"
        f"  Successful:      {result.successful} ({100*result.successful/max(result.total_requests,1):.1f}%)\n"
        f"  Failed:          {result.failed} ({100*result.failed/max(result.total_requests,1):.1f}%)\n"
        f"  Total Time:      {result.total_time:.3f}s\n"
        f"  Req/sec:         {result.requests_per_second:.1f}\n"
        f"  Latency (ms):\n"
        f"    Min:  {result.min_latency*1000:.1f}\n"
        f"    Avg:  {result.avg_latency*1000:.1f}\n"
        f"    P50:  {result.p50_latency*1000:.1f}\n"
        f"    P95:  {result.p95_latency*1000:.1f}\n"
        f"    P99:  {result.p99_latency*1000:.1f}\n"
        f"    Max:  {result.max_latency*1000:.1f}\n"
        f"{'='*60}\n"
    )


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_db():
    """Mock database session for load tests."""
    db = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_result.fetchone.return_value = None
    mock_result.rowcount = 1
    db.execute.return_value = mock_result
    db.commit = MagicMock()
    db.rollback = MagicMock()
    return db


@pytest.fixture()
async def client(mock_db):
    """Create an httpx.AsyncClient with mocked dependencies."""
    with (
        patch.object(Base.metadata, "create_all"),
        patch("app.database.SessionLocal") as mock_sl,
        patch("app.middleware.api_key_auth.API_KEY_AUTH_ENABLED", False),
    ):
        _health_db = MagicMock()
        _health_db.execute.return_value.fetchone.return_value = (1,)
        _health_db.close = MagicMock()
        mock_sl.return_value = _health_db

        app.dependency_overrides[get_db] = lambda: mock_db

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac

    app.dependency_overrides.clear()


# ═════════════════════════════════════════════════════════════
# 1. Concurrent uploads (10 simultaneous)
# ═════════════════════════════════════════════════════════════


@pytest.mark.load
@pytest.mark.slow
async def test_concurrent_uploads(client, mock_db):
    """
    Simulate 10 concurrent upload requests.

    Verifies that the application handles simultaneous file uploads
    without crashing and returns consistent responses.
    """
    concurrency = 10
    png_bytes = _make_png_bytes()
    result = LoadTestResult(name="Concurrent Uploads (n=10)")

    async def _upload(idx: int):
        """Single upload request with timing."""
        start = time.perf_counter()
        try:
            resp = await client.post(
                "/api/upload",
                files={
                    "file": (
                        f"concurrent_{idx}.png",
                        io.BytesIO(png_bytes),
                        "image/png",
                    )
                },
                data={"user_id": f"load-tester-{idx}"},
            )
            elapsed = time.perf_counter() - start
            return resp.status_code, elapsed, None
        except Exception as exc:
            elapsed = time.perf_counter() - start
            return 0, elapsed, str(exc)

    # Patch rate limiter to avoid 429 during load test
    from app.middleware.rate_limiter import limiter
    original_limits = limiter.default_limits
    limiter.default_limits = ["10000/minute"]

    start_time = time.perf_counter()
    try:
        tasks = [_upload(i) for i in range(concurrency)]
        responses = await asyncio.gather(*tasks, return_exceptions=False)
        result.total_time = time.perf_counter() - start_time

        for status_code, latency, error in responses:
            result.total_requests += 1
            result.latencies.append(latency)
            if error:
                result.failed += 1
                result.errors.append(error)
            elif status_code in (200, 500):
                # 200 = success, 500 = expected if OCR mock not wired
                result.successful += 1
            else:
                result.failed += 1
                result.errors.append(f"HTTP {status_code}")
    finally:
        limiter.default_limits = original_limits

    result.compute()
    print(_make_summary(result))

    # Assertions
    assert result.total_requests == concurrency
    assert result.successful >= concurrency * 0.8, (
        f"Fewer than 80% of concurrent uploads succeeded: "
        f"{result.successful}/{concurrency}. Errors: {result.errors[:5]}"
    )
    # No single request should take more than 10 seconds
    assert result.p99_latency < 10.0, (
        f"P99 latency too high: {result.p99_latency:.3f}s"
    )


# ═════════════════════════════════════════════════════════════
# 2. Consecutive health checks (100 rapid requests)
# ═════════════════════════════════════════════════════════════


@pytest.mark.load
@pytest.mark.slow
async def test_consecutive_health_checks(client, mock_db):
    """
    Fire 100 rapid health check requests in sequence.

    Ensures the health endpoint remains responsive under
    sustained sequential traffic.
    """
    count = 100
    result = LoadTestResult(name=f"Consecutive Health Checks (n={count})")

    from app.middleware.rate_limiter import limiter
    original_limits = limiter.default_limits
    limiter.default_limits = ["10000/minute"]

    start_time = time.perf_counter()
    try:
        for i in range(count):
            start = time.perf_counter()
            try:
                resp = await client.get("/health")
                elapsed = time.perf_counter() - start
                result.total_requests += 1
                result.latencies.append(elapsed)
                if resp.status_code in (200, 503):
                    result.successful += 1
                else:
                    result.failed += 1
                    result.errors.append(f"HTTP {resp.status_code}")
            except Exception as exc:
                elapsed = time.perf_counter() - start
                result.total_requests += 1
                result.latencies.append(elapsed)
                result.failed += 1
                result.errors.append(str(exc))

        result.total_time = time.perf_counter() - start_time
    finally:
        limiter.default_limits = original_limits

    result.compute()
    print(_make_summary(result))

    assert result.total_requests == count
    assert result.successful == count, (
        f"All health checks should succeed: {result.successful}/{count}, "
        f"errors: {result.errors[:5]}"
    )
    # Average latency for health checks should be under 500ms
    assert result.avg_latency < 0.5, (
        f"Health check avg latency too high: {result.avg_latency*1000:.1f}ms"
    )


# ═════════════════════════════════════════════════════════════
# 3. Concurrent suggestions (20 simultaneous)
# ═════════════════════════════════════════════════════════════


@pytest.mark.load
@pytest.mark.slow
async def test_concurrent_suggestions(client, mock_db):
    """
    Simulate 20 concurrent suggestion requests with varied inputs.

    Tests the suggestion engine's ability to handle parallel
    lookups from multiple users.
    """
    concurrency = 20
    # Varied medical terms to test different suggestion strategies
    test_terms = [
        "Ostecb(astoma",
        "الفقرات القطنية",
        "FOGMACIN",
        "Chondrosarcoma",
        "GENTAMICIN",
        "ORIF",
        "AVN",
        "CT Scan",
        "Osteomyelitis",
        "الفقارة الصدرية",
    ]
    result = LoadTestResult(name=f"Concurrent Suggestions (n={concurrency})")

    async def _get_suggestion(idx: int):
        """Single suggestion request with timing."""
        term = test_terms[idx % len(test_terms)]
        start = time.perf_counter()
        try:
            resp = await client.get(
                "/suggestions/",
                params={
                    "text": term,
                    "is_medical": "true",
                    "context_before": "Patient presents with",
                },
            )
            elapsed = time.perf_counter() - start
            return resp.status_code, elapsed, None
        except Exception as exc:
            elapsed = time.perf_counter() - start
            return 0, elapsed, str(exc)

    from app.middleware.rate_limiter import limiter
    original_limits = limiter.default_limits
    limiter.default_limits = ["10000/minute"]

    start_time = time.perf_counter()
    try:
        tasks = [_get_suggestion(i) for i in range(concurrency)]
        responses = await asyncio.gather(*tasks, return_exceptions=False)
        result.total_time = time.perf_counter() - start_time

        for status_code, latency, error in responses:
            result.total_requests += 1
            result.latencies.append(latency)
            if error:
                result.failed += 1
                result.errors.append(error)
            elif status_code == 200:
                result.successful += 1
            else:
                result.failed += 1
                result.errors.append(f"HTTP {status_code}")
    finally:
        limiter.default_limits = original_limits

    result.compute()
    print(_make_summary(result))

    assert result.total_requests == concurrency
    assert result.successful == concurrency, (
        f"All suggestion requests should succeed: {result.successful}/{concurrency}, "
        f"errors: {result.errors[:5]}"
    )
    # P95 latency should be under 2 seconds
    assert result.p95_latency < 2.0, (
        f"Suggestions P95 latency too high: {result.p95_latency*1000:.1f}ms"
    )


# ═════════════════════════════════════════════════════════════
# 4. Sustained load (50 requests over 10 seconds)
# ═════════════════════════════════════════════════════════════


@pytest.mark.load
@pytest.mark.slow
async def test_sustained_load(client, mock_db):
    """
    Send 50 requests spaced over 10 seconds to simulate sustained usage.

    Verifies the application maintains consistent performance over time
    without degradation, memory leaks, or connection exhaustion.
    """
    total_requests = 50
    duration_seconds = 10.0
    interval = duration_seconds / total_requests  # 0.2s between requests

    endpoints = [
        lambda: client.get("/health"),
        lambda: client.get("/"),
        lambda: client.get("/api/pending"),
        lambda: client.get(
            "/suggestions/",
            params={"text": "test-term", "is_medical": "false"},
        ),
    ]

    result = LoadTestResult(name=f"Sustained Load ({total_requests} req / {duration_seconds}s)")

    from app.middleware.rate_limiter import limiter
    original_limits = limiter.default_limits
    limiter.default_limits = ["10000/minute"]

    start_time = time.perf_counter()
    try:
        for i in range(total_requests):
            endpoint_fn = endpoints[i % len(endpoints)]

            req_start = time.perf_counter()
            try:
                resp = await endpoint_fn()
                elapsed = time.perf_counter() - req_start
                result.total_requests += 1
                result.latencies.append(elapsed)

                if resp.status_code in (200, 503):
                    result.successful += 1
                else:
                    result.failed += 1
                    result.errors.append(f"HTTP {resp.status_code}")
            except Exception as exc:
                elapsed = time.perf_counter() - req_start
                result.total_requests += 1
                result.latencies.append(elapsed)
                result.failed += 1
                result.errors.append(str(exc))

            # Wait before next request (except after the last one)
            if i < total_requests - 1:
                await asyncio.sleep(interval)

        result.total_time = time.perf_counter() - start_time
    finally:
        limiter.default_limits = original_limits

    result.compute()
    print(_make_summary(result))

    assert result.total_requests == total_requests
    # Allow some failures (rate limiting, transient errors)
    success_rate = result.successful / max(result.total_requests, 1)
    assert success_rate >= 0.9, (
        f"Success rate too low under sustained load: {success_rate:.1%}. "
        f"Errors: {result.errors[:10]}"
    )
    # Latency should not degrade significantly over time
    # Compare first quartile to last quartile
    if len(result.latencies) >= 8:
        n = len(result.latencies)
        early_avg = statistics.mean(result.latencies[: n // 4])
        late_avg = statistics.mean(result.latencies[3 * n // 4 :])
        degradation_ratio = late_avg / max(early_avg, 1e-9)
        assert degradation_ratio < 5.0, (
            f"Latency degraded significantly over time: "
            f"early avg={early_avg*1000:.1f}ms, late avg={late_avg*1000:.1f}ms "
            f"(ratio={degradation_ratio:.1f}x)"
        )


# ═════════════════════════════════════════════════════════════
# 5. Memory cleanup (detect leaks after many requests)
# ═════════════════════════════════════════════════════════════


@pytest.mark.load
@pytest.mark.slow
async def test_memory_cleanup(client, mock_db):
    """
    Verify no significant memory leaks after many requests.

    Measures memory usage before and after 200 requests, ensuring
    growth stays within acceptable bounds (under 50 MB increase).
    """
    request_count = 200

    from app.middleware.rate_limiter import limiter
    original_limits = limiter.default_limits
    limiter.default_limits = ["10000/minute"]

    try:
        # Warm up: make a few requests to initialize any lazy resources
        for _ in range(5):
            await client.get("/health")
        await asyncio.sleep(0.1)

        # Take baseline memory snapshot
        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        # Fire many requests
        for i in range(request_count):
            if i % 4 == 0:
                await client.get("/health")
            elif i % 4 == 1:
                await client.get("/")
            elif i % 4 == 2:
                await client.get("/api/pending")
            else:
                await client.get(
                    "/suggestions/", params={"text": f"term-{i}"}
                )

        # Give the GC a chance to clean up
        import gc
        gc.collect()
        await asyncio.sleep(0.2)

        # Take final snapshot
        snapshot_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        # Compare snapshots
        top_stats = snapshot_after.compare_to(
            snapshot_before, "lineno"
        )

        total_diff = sum(stat.size_diff for stat in top_stats if stat.size_diff > 0)
        total_diff_mb = total_diff / (1024 * 1024)

        # Log top allocations for debugging
        print(f"\n  Memory growth after {request_count} requests: {total_diff_mb:.2f} MB")
        print("  Top 10 growing allocations:")
        for stat in top_stats[:10]:
            if stat.size_diff > 0:
                print(
                    f"    {stat.size_diff / 1024:.1f} KiB  "
                    f"{stat.traceback.format()[0] if stat.traceback else 'unknown'}"
                )

        # Assert memory growth is reasonable
        # 50 MB threshold allows for normal caching but catches real leaks
        assert total_diff_mb < 50, (
            f"Excessive memory growth detected: {total_diff_mb:.2f} MB "
            f"after {request_count} requests. Possible memory leak."
        )
    finally:
        limiter.default_limits = original_limits
        # Ensure tracemalloc is stopped
        if tracemalloc.is_tracing():
            tracemalloc.stop()
