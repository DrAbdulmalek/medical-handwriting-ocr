"""
Virus scanning module for Medical Handwriting OCR.

Provides two complementary scanning backends:

1. **ClamAV** – Open-source anti-virus engine scanning files locally via a
   ClamAV daemon (``clamd``).  Ideal for on-premise and private-cloud
   deployments where files never leave the network boundary.

2. **VirusTotal** – Cloud-based multi-engine scan API.  Files are hashed and
   looked up against 70+ commercial AV engines.  Requires a VirusTotal API
   key.

Both backends are optional and enabled individually via environment
variables.  When both are enabled, the validator can run them in parallel
and merge results.

Usage
-----
    from app.validators.virus_scanner import VirusScanner

    scanner = VirusScanner()
    is_clean, report = await scanner.scan_bytes(file_bytes)
    if not is_clean:
        raise HTTPException(403, detail=f"Malware detected: {report}")
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (environment-driven)
# ---------------------------------------------------------------------------

_CLAMAV_HOST: str = os.getenv("CLAMAV_HOST", "localhost")
_CLAMAV_PORT: int = int(os.getenv("CLAMAV_PORT", "3310"))
_CLAMAV_TIMEOUT: int = int(os.getenv("CLAMAV_TIMEOUT", "30"))

_VIRUSTOTAL_API_KEY: str = os.getenv("VIRUSTOTAL_API_KEY", "")
_VIRUSTOTAL_SCAN_URL: str = os.getenv(
    "VIRUSTOTAL_API_URL", "https://www.virustotal.com/api/v3"
)
_VIRUSTOTAL_TIMEOUT: int = int(os.getenv("VIRUSTOTAL_TIMEOUT", "60"))
_VIRUSTOTAL_DETECTION_THRESHOLD: int = int(
    os.getenv("VIRUSTOTAL_DETECTION_THRESHOLD", "1")
)

_SCANNER_ENABLED: bool = os.getenv("VIRUS_SCANNER_ENABLED", "true").lower() == "true"
_CLAMAV_ENABLED: bool = os.getenv("CLAMAV_ENABLED", "true").lower() == "true"
_VIRUSTOTAL_ENABLED: bool = (
    _VIRUSTOTAL_API_KEY != "" and os.getenv("VIRUSTOTAL_ENABLED", "false").lower() == "true"
)

logger.info(
    "Virus scanner initialised",
    extra={
        "fields": {
            "scanner_enabled": _SCANNER_ENABLED,
            "clamav_enabled": _CLAMAV_ENABLED,
            "clamav_host": _CLAMAV_HOST,
            "clamav_port": _CLAMAV_PORT,
            "virustotal_enabled": _VIRUSTOTAL_ENABLED,
            "virustotal_threshold": _VIRUSTOTAL_DETECTION_THRESHOLD,
        }
    },
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class ScanBackend(str, Enum):
    CLAMAV = "clamav"
    VIRUSTOTAL = "virustotal"


@dataclass
class ScanResult:
    """Result of a single virus scan backend.

    Attributes:
        backend: Which scanner produced this result.
        is_clean: ``True`` if no threats were detected.
        threat_name: Name of the detected threat, or empty string if clean.
        details: Raw response / engine-specific data for audit logging.
    """

    backend: ScanBackend
    is_clean: bool
    threat_name: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        if self.is_clean:
            return f"[{self.backend.value}] Clean"
        return f"[{self.backend.value}] THREAT: {self.threat_name}"


@dataclass
class VirusScanReport:
    """Aggregated report from all enabled scanners.

    Attributes:
        is_clean: ``True`` only if **every** backend reports clean.
        results: Individual results keyed by backend name.
        file_hash: SHA-256 hash of the scanned content (for audit trail).
        file_size: Size of the scanned content in bytes.
    """

    is_clean: bool = True
    results: Dict[str, ScanResult] = field(default_factory=dict)
    file_hash: str = ""
    file_size: int = 0

    def add_result(self, result: ScanResult) -> None:
        """Merge a single backend result into the aggregate report."""
        self.results[result.backend.value] = result
        if not result.is_clean:
            self.is_clean = False

    @property
    def threats(self) -> List[str]:
        """Return all threat names across backends."""
        return [
            r.threat_name
            for r in self.results.values()
            if not r.is_clean and r.threat_name
        ]

    @property
    def summary(self) -> str:
        lines: List[str] = []
        for name, result in self.results.items():
            lines.append(result.summary)
        status = "CLEAN" if self.is_clean else "THREAT DETECTED"
        lines.append(f"Final verdict: {status} (SHA-256: {self.file_hash[:16]}...)")
        return " | ".join(lines)


# ---------------------------------------------------------------------------
# ClamAV Scanner
# ---------------------------------------------------------------------------

class ClamAVScanner:
    """Scans files using a local ClamAV daemon over TCP socket.

    The ClamAV daemon (``clamd``) must be running and accessible.  For
    Docker deployments, include a ClamAV service sidecar::

        services:
          clamav:
            image: clamav/clamav:latest
            ports:
              - "3310:3310"
    """

    def __init__(
        self,
        host: str = _CLAMAV_HOST,
        port: int = _CLAMAV_PORT,
        timeout: int = _CLAMAV_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    async def scan_bytes(self, data: bytes) -> ScanResult:
        """Scan raw bytes via ClamAV ``INSTREAM`` command.

        Args:
            data: File content to scan.

        Returns:
            :class:`ScanResult` with the ClamAV findings.
        """
        if not data:
            return ScanResult(
                backend=ScanBackend.CLAMAV,
                is_clean=True,
                details={"reason": "empty_file"},
            )

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )

            # INSTREAM: scan data from a stream
            writer.write(b"zINSTREAM\x00")
            # Send data in 16KB chunks (ClamAV max chunk size)
            chunk_size = 16384
            offset = 0
            while offset < len(data):
                chunk = data[offset : offset + chunk_size]
                size_prefix = len(chunk).to_bytes(4, "little")
                writer.write(size_prefix + chunk)
                offset += chunk_size
            # Empty chunk signals end of stream
            writer.write((0).to_bytes(4, "little"))
            await writer.drain()

            # Read response – ClamAV terminates with null byte
            response_bytes = await asyncio.wait_for(
                reader.readuntil(b"\x00"),
                timeout=self.timeout,
            )
            writer.close()
            await writer.wait_closed()

            response = response_bytes.decode("utf-8", errors="replace").strip()

            # ClamAV responses:
            #   "stream: OK"                    → clean
            #   "stream: Eicar-Test-Signature FOUND" → infected
            #   "stream: {error message} ERROR" → error
            if response.endswith(" FOUND"):
                # Extract threat name
                parts = response.split(" FOUND")
                threat = parts[0].split(": ", 1)[-1] if ": " in parts[0] else parts[0]
                logger.warning(
                    "ClamAV detected threat: %s", threat,
                    extra={"fields": {"clamav_response": response, "threat": threat}},
                )
                return ScanResult(
                    backend=ScanBackend.CLAMAV,
                    is_clean=False,
                    threat_name=threat,
                    details={"raw_response": response, "status": "found"},
                )
            elif response.endswith(" ERROR"):
                error_msg = response.split(": ", 1)[-1] if ": " in response else response
                logger.error(
                    "ClamAV scan error: %s", error_msg,
                    extra={"fields": {"clamav_response": response}},
                )
                # On error, we return clean to avoid blocking legitimate uploads
                # due to scanner misconfiguration. The error is logged for ops.
                return ScanResult(
                    backend=ScanBackend.CLAMAV,
                    is_clean=True,
                    details={"raw_response": response, "status": "error", "error": error_msg},
                )
            else:
                # "stream: OK" or any other non-threat response
                logger.debug("ClamAV scan passed: %s", response)
                return ScanResult(
                    backend=ScanBackend.CLAMAV,
                    is_clean=True,
                    details={"raw_response": response, "status": "ok"},
                )

        except asyncio.TimeoutError:
            logger.error(
                "ClamAV connection timed out (host=%s, port=%d, timeout=%ds)",
                self.host, self.port, self.timeout,
            )
            return ScanResult(
                backend=ScanBackend.CLAMAV,
                is_clean=True,
                details={"status": "timeout", "error": "connection_timeout"},
            )
        except ConnectionRefusedError:
            logger.error(
                "ClamAV daemon not reachable (host=%s, port=%d). "
                "Ensure ClamAV service is running.",
                self.host, self.port,
            )
            return ScanResult(
                backend=ScanBackend.CLAMAV,
                is_clean=True,
                details={"status": "unreachable", "error": "connection_refused"},
            )
        except OSError as exc:
            logger.error("ClamAV socket error: %s", exc)
            return ScanResult(
                backend=ScanBackend.CLAMAV,
                is_clean=True,
                details={"status": "os_error", "error": str(exc)},
            )

    async def ping(self) -> bool:
        """Check if ClamAV daemon is reachable.

        Returns:
            ``True`` if the daemon responds to a ``PING`` command.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=5,
            )
            writer.write(b"zPING\x00")
            await writer.drain()

            response = await asyncio.wait_for(
                reader.readuntil(b"\x00"),
                timeout=5,
            )
            writer.close()
            await writer.wait_closed()

            return b"PONG" in response
        except Exception:
            return False


# ---------------------------------------------------------------------------
# VirusTotal Scanner
# ---------------------------------------------------------------------------

class VirusTotalScanner:
    """Scans files using the VirusTotal v3 API.

    This scanner hashes the file content and queries VirusTotal for existing
    reports.  If no report exists, it optionally uploads the file for a fresh
    scan.

    Requires a VirusTotal API key set via ``VIRUSTOTAL_API_KEY``.

    Rate limit: 4 requests/minute on the free tier, 600/minute on premium.
    """

    def __init__(
        self,
        api_key: str = _VIRUSTOTAL_API_KEY,
        api_url: str = _VIRUSTOTAL_SCAN_URL,
        timeout: int = _VIRUSTOTAL_TIMEOUT,
        detection_threshold: int = _VIRUSTOTAL_DETECTION_THRESHOLD,
    ) -> None:
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.detection_threshold = detection_threshold
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def headers(self) -> Dict[str, str]:
        return {"x-apikey": self.api_key}

    def _hash_file(self, data: bytes) -> Tuple[str, str, str]:
        """Compute SHA-256, SHA-1, and MD5 hashes of the file content.

        Returns:
            Tuple of (sha256, sha1, md5) hex digest strings.
        """
        sha256 = hashlib.sha256(data).hexdigest()
        sha1 = hashlib.sha1(data).hexdigest()
        md5 = hashlib.md5(data).hexdigest()
        return (sha256, sha1, md5)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-initialise the HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def scan_bytes(self, data: bytes) -> ScanResult:
        """Look up (and optionally upload) a file on VirusTotal.

        The workflow:
        1. Hash the file content.
        2. Query VirusTotal for an existing report by SHA-256 hash.
        3. If found, evaluate detection ratio against the threshold.
        4. If not found, upload the file for scanning and wait for result.

        Args:
            data: File content to scan.

        Returns:
            :class:`ScanResult` with the VirusTotal findings.
        """
        if not data:
            return ScanResult(
                backend=ScanBackend.VIRUSTOTAL,
                is_clean=True,
                details={"reason": "empty_file"},
            )

        if not self.api_key:
            logger.warning("VirusTotal API key not configured, skipping scan")
            return ScanResult(
                backend=ScanBackend.VIRUSTOTAL,
                is_clean=True,
                details={"status": "skipped", "reason": "no_api_key"},
            )

        sha256, sha1, md5 = self._hash_file(data)
        session = await self._get_session()

        try:
            # Step 1: Look up existing report
            lookup_url = f"{self.api_url}/files/{sha256}"
            async with session.get(lookup_url) as resp:
                if resp.status == 200:
                    report_data = await resp.json()
                    return self._evaluate_report(report_data, sha256)
                elif resp.status == 404:
                    # File not previously scanned – upload it
                    logger.info(
                        "File not found on VirusTotal, uploading for scan "
                        "(sha256=%s, size=%d bytes)",
                        sha256[:16], len(data),
                    )
                    return await self._upload_and_wait(data, sha256, session)
                elif resp.status == 401:
                    logger.error("VirusTotal API key invalid or expired")
                    return ScanResult(
                        backend=ScanBackend.VIRUSTOTAL,
                        is_clean=True,
                        details={"status": "auth_error", "error": "invalid_api_key"},
                    )
                elif resp.status == 429:
                    logger.warning("VirusTotal rate limit exceeded")
                    return ScanResult(
                        backend=ScanBackend.VIRUSTOTAL,
                        is_clean=True,
                        details={"status": "rate_limited", "error": "too_many_requests"},
                    )
                else:
                    text = await resp.text()
                    logger.error(
                        "VirusTotal unexpected response (status=%d): %s",
                        resp.status, text[:200],
                    )
                    return ScanResult(
                        backend=ScanBackend.VIRUSTOTAL,
                        is_clean=True,
                        details={"status": "error", "error": f"http_{resp.status}"},
                    )
        except asyncio.TimeoutError:
            logger.error("VirusTotal request timed out")
            return ScanResult(
                backend=ScanBackend.VIRUSTOTAL,
                is_clean=True,
                details={"status": "timeout", "error": "request_timeout"},
            )
        except aiohttp.ClientError as exc:
            logger.error("VirusTotal client error: %s", exc)
            return ScanResult(
                backend=ScanBackend.VIRUSTOTAL,
                is_clean=True,
                details={"status": "client_error", "error": str(exc)},
            )

    def _evaluate_report(self, report: Dict[str, Any], sha256: str) -> ScanResult:
        """Evaluate an existing VirusTotal report.

        Args:
            report: Parsed JSON response from the VirusTotal API.
            sha256: SHA-256 hash of the file (for logging).

        Returns:
            :class:`ScanResult` based on the detection ratio.
        """
        attrs = report.get("data", {}).get("attributes", {})
        stats = attrs.get("last_analysis_stats", {})
        total_detections = stats.get("malicious", 0) + stats.get("suspicious", 0)
        total_engines = sum(stats.values()) or 1

        details = {
            "sha256": sha256,
            "total_detections": total_detections,
            "total_engines": total_engines,
            "stats": stats,
            "last_analysis_date": attrs.get("last_analysis_date"),
            "status": "report_found",
        }

        # Extract names of popular threats
        popular_threats = [
            r.get("result", "")
            for r in attrs.get("last_analysis_results", {}).values()
            if r.get("category") == "malicious" and r.get("result")
        ]
        threat_name = ", ".join(set(popular_threats[:3])) if popular_threats else ""

        if total_detections >= self.detection_threshold:
            logger.warning(
                "VirusTotal: %d/%d engines detected threats (sha256=%s)",
                total_detections, total_engines, sha256[:16],
                extra={"fields": details},
            )
            return ScanResult(
                backend=ScanBackend.VIRUSTOTAL,
                is_clean=False,
                threat_name=threat_name or f"detected_by_{total_detections}_engines",
                details=details,
            )

        logger.debug(
            "VirusTotal scan passed: %d/%d detections (sha256=%s)",
            total_detections, total_engines, sha256[:16],
        )
        return ScanResult(
            backend=ScanBackend.VIRUSTOTAL,
            is_clean=True,
            details=details,
        )

    async def _upload_and_wait(
        self,
        data: bytes,
        sha256: str,
        session: aiohttp.ClientSession,
    ) -> ScanResult:
        """Upload file to VirusTotal and poll for analysis results.

        Args:
            data: File content to upload.
            sha256: SHA-256 hash of the file.
            session: Active aiohttp session.

        Returns:
            :class:`ScanResult` based on the scan results.
        """
        try:
            # Upload the file
            upload_url = f"{self.api_url}/files"
            # VirusTotal requires multipart/form-data with a "file" field
            form_data = aiohttp.FormData()
            form_data.add_field(
                "file",
                data,
                filename="upload_scan",
                content_type="application/octet-stream",
            )

            async with session.post(upload_url, data=form_data) as resp:
                if resp.status == 200:
                    upload_data = await resp.json()
                    analysis_id = upload_data.get("data", {}).get("id", "")
                    logger.info(
                        "VirusTotal upload accepted (analysis_id=%s, sha256=%s)",
                        analysis_id, sha256[:16],
                    )
                else:
                    text = await resp.text()
                    logger.error(
                        "VirusTotal upload failed (status=%d): %s",
                        resp.status, text[:200],
                    )
                    return ScanResult(
                        backend=ScanBackend.VIRUSTOTAL,
                        is_clean=True,
                        details={"status": "upload_failed", "error": f"http_{resp.status}"},
                    )

            # Poll for analysis results (up to 60 seconds)
            analysis_url = f"{self.api_url}/analyses/{analysis_id}"
            max_polls = 12  # 12 × 5s = 60s
            for attempt in range(max_polls):
                await asyncio.sleep(5)
                async with session.get(analysis_url) as poll_resp:
                    if poll_resp.status == 200:
                        poll_data = await poll_resp.json()
                        status = poll_data.get("data", {}).get("attributes", {}).get("status", "")
                        if status == "completed":
                            # Get the file report now
                            return await self._get_report_after_upload(sha256, session)
                        elif status == "queued":
                            logger.debug(
                                "VirusTotal analysis still queued (attempt %d/%d)",
                                attempt + 1, max_polls,
                            )
                            continue
                        elif status == "failed":
                            logger.error("VirusTotal analysis failed for sha256=%s", sha256[:16])
                            return ScanResult(
                                backend=ScanBackend.VIRUSTOTAL,
                                is_clean=True,
                                details={"status": "analysis_failed"},
                            )
                    else:
                        continue

            # Polling timed out
            logger.warning(
                "VirusTotal analysis polling timed out (sha256=%s)", sha256[:16],
            )
            return ScanResult(
                backend=ScanBackend.VIRUSTOTAL,
                is_clean=True,
                details={"status": "poll_timeout"},
            )

        except asyncio.TimeoutError:
            logger.error("VirusTotal upload/poll timed out")
            return ScanResult(
                backend=ScanBackend.VIRUSTOTAL,
                is_clean=True,
                details={"status": "timeout", "error": "upload_timeout"},
            )
        except aiohttp.ClientError as exc:
            logger.error("VirusTotal upload error: %s", exc)
            return ScanResult(
                backend=ScanBackend.VIRUSTOTAL,
                is_clean=True,
                details={"status": "client_error", "error": str(exc)},
            )

    async def _get_report_after_upload(
        self, sha256: str, session: aiohttp.ClientSession
    ) -> ScanResult:
        """Fetch the report after a successful upload analysis.

        Args:
            sha256: SHA-256 hash of the file.
            session: Active aiohttp session.

        Returns:
            :class:`ScanResult` based on the analysis results.
        """
        lookup_url = f"{self.api_url}/files/{sha256}"
        try:
            async with session.get(lookup_url) as resp:
                if resp.status == 200:
                    report_data = await resp.json()
                    return self._evaluate_report(report_data, sha256)
                else:
                    return ScanResult(
                        backend=ScanBackend.VIRUSTOTAL,
                        is_clean=True,
                        details={"status": "report_unavailable_after_upload"},
                    )
        except Exception as exc:
            logger.error("VirusTotal report fetch error after upload: %s", exc)
            return ScanResult(
                backend=ScanBackend.VIRUSTOTAL,
                is_clean=True,
                details={"status": "error", "error": str(exc)},
            )


# ---------------------------------------------------------------------------
# Main Scanner (orchestrator)
# ---------------------------------------------------------------------------

class VirusScanner:
    """High-level virus scanning orchestrator.

    Coordinates enabled backends and produces a single aggregated report.
    Backends run concurrently where possible.

    Usage::

        scanner = VirusScanner()
        report = await scanner.scan_bytes(file_bytes)
        if not report.is_clean:
            # Reject upload
            pass
    """

    def __init__(self) -> None:
        self.clamav = ClamAVScanner() if _CLAMAV_ENABLED else None
        self.virustotal = VirusTotalScanner() if _VIRUSTOTAL_ENABLED else None

        if not _SCANNER_ENABLED:
            logger.info("Virus scanning is disabled via VIRUS_SCANNER_ENABLED=false")
            return

        if not self.clamav and not self.virustotal:
            logger.warning(
                "No virus scanner backends enabled. "
                "Set CLAMAV_ENABLED=true and/or VIRUSTOTAL_ENABLED=true "
                "with VIRUSTOTAL_API_KEY."
            )

    @property
    def is_enabled(self) -> bool:
        """``True`` if virus scanning is enabled and at least one backend is active."""
        return _SCANNER_ENABLED and (self.clamav is not None or self.virustotal is not None)

    async def scan_bytes(self, data: bytes) -> VirusScanReport:
        """Scan file content with all enabled backends.

        Backends run concurrently.  If one backend fails (e.g., ClamAV is
        unreachable), the other backend's result is still used.  If a backend
        encounters an error, it returns ``is_clean=True`` (fail-open) to avoid
        blocking legitimate uploads due to scanner misconfiguration.

        Args:
            data: Raw file bytes to scan.

        Returns:
            :class:`VirusScanReport` with aggregated results from all backends.
        """
        report = VirusScanReport(
            file_hash=hashlib.sha256(data).hexdigest() if data else "",
            file_size=len(data),
        )

        if not self.is_enabled:
            logger.debug("Virus scanning disabled, returning clean report")
            return report

        # Collect async tasks for all enabled backends
        tasks: List[Tuple[str, asyncio.Task]] = []
        if self.clamav:
            tasks.append(("clamav", asyncio.create_task(self.clamav.scan_bytes(data))))
        if self.virustotal:
            tasks.append(("virustotal", asyncio.create_task(self.virustotal.scan_bytes(data))))

        # Run all scans concurrently
        if tasks:
            results = await asyncio.gather(
                *[task for _, task in tasks],
                return_exceptions=True,
            )
            for (name, _), result in zip(tasks, results):
                if isinstance(result, ScanResult):
                    report.add_result(result)
                elif isinstance(result, Exception):
                    logger.error("Virus scanner '%s' raised: %s", name, result)
                    # Fail-open: don't block on scanner crashes
                    report.add_result(ScanResult(
                        backend=ScanBackend(name),
                        is_clean=True,
                        details={"status": "exception", "error": str(result)},
                    ))

        logger.info(
            "Virus scan complete: clean=%s, backends=%s, hash=%s",
            report.is_clean,
            list(report.results.keys()),
            report.file_hash[:16],
            extra={"fields": {
                "is_clean": report.is_clean,
                "file_hash": report.file_hash,
                "file_size": report.file_size,
                "backends": list(report.results.keys()),
                "threats": report.threats,
            }},
        )
        return report

    async def health_check(self) -> Dict[str, Any]:
        """Check the health of all enabled scanner backends.

        Returns:
            Dictionary with backend names as keys and status booleans.
        """
        health: Dict[str, Any] = {"enabled": self.is_enabled}

        if self.clamav:
            health["clamav"] = await self.clamav.ping()

        if self.virustotal:
            # VirusTotal health check: try a simple API call
            try:
                session = await self.virustotal._get_session()
                test_url = f"{self.virustotal.api_url}/domains/google.com"
                async with session.get(test_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    health["virustotal"] = resp.status in (200, 404)  # 404 = API works, domain not found
            except Exception:
                health["virustotal"] = False

        return health

    async def close(self) -> None:
        """Clean up resources (HTTP sessions)."""
        if self.virustotal:
            await self.virustotal.close()


# ---------------------------------------------------------------------------
# Convenience function for direct use in validators / routers
# ---------------------------------------------------------------------------

_scanner_instance: Optional[VirusScanner] = None


async def get_virus_scanner() -> VirusScanner:
    """Get or create a singleton VirusScanner instance.

    This avoids creating new HTTP sessions on every request.
    """
    global _scanner_instance
    if _scanner_instance is None:
        _scanner_instance = VirusScanner()
    return _scanner_instance


async def scan_upload(
    data: bytes,
    filename: str = "upload",
) -> VirusScanReport:
    """One-shot convenience function to scan uploaded data.

    Args:
        data: Raw file bytes.
        filename: Original filename (for logging only).

    Returns:
        :class:`VirusScanReport` with the scan results.
    """
    scanner = await get_virus_scanner()
    report = await scanner.scan_bytes(data)
    logger.info(
        "Virus scan for '%s': clean=%s, threats=%s",
        filename,
        report.is_clean,
        report.threats,
    )
    return report
