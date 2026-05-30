"""
File upload validation for Medical Handwriting OCR.

Provides defence-in-depth validation of user-uploaded files:

1. **File size validation** – rejects files exceeding the configurable size cap.
2. **Magic-byte (signature) validation** – verifies actual file content matches
   the claimed type, independent of the filename extension.
3. **Content-Type verification** – ensures the HTTP Content-Type header is
   consistent with the detected file type.
4. **Filename sanitisation** – strips path-traversal components and dangerous
   characters so that filenames are safe to persist or log.

All limits are configurable via environment variables so operators can tighten
or relax policy per deployment without changing code.
"""

from __future__ import annotations

import logging
import os
import re
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment-driven configuration
# ---------------------------------------------------------------------------

_UPLOAD_MAX_SIZE_MB: int = int(os.getenv("UPLOAD_MAX_SIZE_MB", "10"))
_MAX_PAGE_COUNT: int = int(os.getenv("UPLOAD_MAX_PAGE_COUNT", "50"))

_UPLOAD_ALLOWED_EXTENSIONS_RAW: str = os.getenv(
    "UPLOAD_ALLOWED_EXTENSIONS", "jpg,jpeg,png,gif,bmp,tiff,tif,webp,dcm,pdf"
)
_UPLOAD_BLOCKED_EXTENSIONS_RAW: str = os.getenv(
    "UPLOAD_BLOCKED_EXTENSIONS", "exe,sh,bat,cmd,ps1,py,js"
)

ALLOWED_EXTENSIONS: List[str] = [
    ext.strip().lower().lstrip(".")
    for ext in _UPLOAD_ALLOWED_EXTENSIONS_RAW.split(",")
    if ext.strip()
]

BLOCKED_EXTENSIONS: set = {
    ext.strip().lower().lstrip(".")
    for ext in _UPLOAD_BLOCKED_EXTENSIONS_RAW.split(",")
    if ext.strip()
}

MAX_FILE_SIZE_BYTES: int = _UPLOAD_MAX_SIZE_MB * 1024 * 1024

logger.info(
    "Upload validator initialised",
    extra={
        "fields": {
            "max_size_mb": _UPLOAD_MAX_SIZE_MB,
            "max_size_bytes": MAX_FILE_SIZE_BYTES,
            "max_pages": _MAX_PAGE_COUNT,
            "allowed_extensions": ALLOWED_EXTENSIONS,
            "blocked_extensions": list(BLOCKED_EXTENSIONS),
        }
    },
)

# ---------------------------------------------------------------------------
# Magic-byte signatures
# ---------------------------------------------------------------------------

# Each entry maps a human-readable type label to a list of signature checkers.
# A checker is a callable ``(contents: bytes) -> bool``.

_SIGNATURES: dict[str, list] = {
    "jpeg": [
        # JPEG files start with FF D8 FF
        lambda c: c[:3] == b"\xff\xd8\xff",
    ],
    "png": [
        # PNG files start with the 8-byte signature 89 50 4E 47 0D 0A 1A 0A
        lambda c: c[:8] == b"\x89PNG\r\n\x1a\n",
    ],
    "gif": [
        lambda c: c[:6] in (b"GIF87a", b"GIF89a"),
    ],
    "bmp": [
        lambda c: c[:2] == b"BM",
    ],
    "tiff": [
        # Little-endian TIFF: II 2A 00
        lambda c: c[:4] == b"II\x2a\x00",
        # Big-endian TIFF: MM 00 2A
        lambda c: c[:4] == b"MM\x00\x2a",
    ],
    "webp": [
        # RIFF....WEBP
        lambda c: len(c) >= 12 and c[:4] == b"RIFF" and c[8:12] == b"WEBP",
    ],
    "dicom": [
        # DICOM preamble is 128 bytes; bytes 128-131 must be "DICM"
        lambda c: len(c) >= 132 and c[128:132] == b"DICM",
    ],
    "pdf": [
        lambda c: c[:4] == b"%PDF",
    ],
}

# Content-Type whitelist – only these MIME types are accepted
_CONTENT_TYPE_WHITELIST: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/bmp",
        "image/tiff",
        "image/webp",
        "application/dicom",
        "application/pdf",
    }
)

# Mapping from detected type label -> acceptable Content-Type strings
_TYPE_TO_CONTENT_TYPES: dict[str, set[str]] = {
    "jpeg": {"image/jpeg"},
    "png": {"image/png"},
    "gif": {"image/gif"},
    "bmp": {"image/bmp"},
    "tiff": {"image/tiff", "image/tiff-fx"},
    "webp": {"image/webp"},
    "dicom": {"application/dicom"},
    "pdf": {"application/pdf"},
}

# Mapping from detected type label -> primary file extension
_TYPE_TO_EXTENSIONS: dict[str, list[str]] = {
    "jpeg": ["jpg", "jpeg"],
    "png": ["png"],
    "gif": ["gif"],
    "bmp": ["bmp"],
    "tiff": ["tiff", "tif"],
    "webp": ["webp"],
    "dicom": ["dcm"],
    "pdf": ["pdf"],
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_allowed_extensions() -> List[str]:
    """Return the list of allowed file extensions (without leading dots).

    The list is derived from the ``UPLOAD_ALLOWED_EXTENSIONS`` environment
    variable (comma-separated) with a sensible default.
    """
    return list(ALLOWED_EXTENSIONS)


def sanitize_filename(filename: str) -> str:
    """Sanitise a user-supplied filename to prevent path-traversal attacks.

    The function:
    * Strips any directory components (``/`` and ``\\``).
    * Removes or replaces characters that are dangerous in filenames on any
      major OS (``<>:"/\\|?*\\x00`` and control characters).
    * Rejects the filename entirely if it matches a blocked extension.

    Args:
        filename: Raw filename supplied by the client (e.g. from
            ``UploadFile.filename``).

    Returns:
        A safe, flat filename string.  If the result is empty or the
        extension is blocked, the string ``"unnamed_upload"`` is returned
        instead and a warning is logged.
    """
    if not filename:
        logger.warning("Empty filename received, using fallback")
        return "unnamed_upload"

    # Strip directory paths – handle both POSIX and Windows separators
    safe = filename.replace("\\", "/")
    safe = safe.rsplit("/", 1)[-1]

    # Remove null bytes
    safe = safe.replace("\x00", "")

    # Replace characters that are illegal or problematic across platforms
    safe = re.sub(r'[<>:"|?*\x00-\x1f]', "_", safe)

    # Collapse multiple consecutive dots (except the final extension dot)
    safe = re.sub(r"\.{2,}", ".", safe)

    # Strip leading/trailing whitespace and dots
    safe = safe.strip(". ")

    # Check against blocked extensions
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    if ext and ext in BLOCKED_EXTENSIONS:
        logger.warning(
            "Blocked extension in filename: %s (extension: %s)", filename, ext,
            extra={"fields": {"original_filename": filename, "blocked_ext": ext}},
        )
        return "unnamed_upload"

    if not safe:
        logger.warning("Filename sanitisation produced empty string from: %s", filename)
        return "unnamed_upload"

    # Truncate extremely long names (255 is a common FS limit)
    if len(safe) > 200:
        safe = safe[:200]

    logger.debug("Sanitised filename: '%s' -> '%s'", filename, safe)
    return safe


# ---------------------------------------------------------------------------
# Core validation checks
# ---------------------------------------------------------------------------

def check_file_size(contents: bytes) -> Tuple[bool, str]:
    """Verify that the uploaded file does not exceed the size limit.

    Args:
        contents: Raw file bytes.

    Returns:
        A tuple ``(is_valid, error_message)``.  When *is_valid* is ``True``
        the *error_message* is an empty string.
    """
    size = len(contents)

    if size == 0:
        msg = "Uploaded file is empty (0 bytes)"
        logger.warning(msg)
        return (False, msg)

    if size > MAX_FILE_SIZE_BYTES:
        size_mb = size / (1024 * 1024)
        msg = (
            f"File size ({size_mb:.2f} MB) exceeds the maximum allowed "
            f"size ({_UPLOAD_MAX_SIZE_MB} MB)"
        )
        logger.warning(
            msg,
            extra={
                "fields": {
                    "file_size_bytes": size,
                    "file_size_mb": round(size_mb, 2),
                    "max_size_mb": _UPLOAD_MAX_SIZE_MB,
                }
            },
        )
        return (False, msg)

    logger.debug(
        "File size check passed: %d bytes (%.2f MB)",
        size,
        size / (1024 * 1024),
    )
    return (True, "")


def check_magic_bytes(contents: bytes) -> Tuple[bool, str, str]:
    """Identify the actual file type by inspecting magic bytes (signatures).

    This is the primary defence against spoofed extensions – an attacker
    cannot rename ``malware.exe`` to ``image.jpg`` and bypass this check.

    Args:
        contents: Raw file bytes (must be at least a few bytes long).

    Returns:
        A tuple ``(is_valid, error_message, detected_type)``.

        * *is_valid* – ``True`` if the content matches a known signature.
        * *error_message* – Human-readable description when the check fails.
        * *detected_type* – One of the keys in ``_SIGNATURES`` when valid,
          or ``"unknown"`` otherwise.
    """
    if not contents or len(contents) < 4:
        msg = "File content is too short to identify type"
        logger.warning(msg, extra={"fields": {"content_length": len(contents) if contents else 0}})
        return (False, msg, "unknown")

    for type_label, checkers in _SIGNATURES.items():
        for checker in checkers:
            try:
                if checker(contents):
                    logger.debug(
                        "Magic-byte check passed: detected type '%s'", type_label,
                        extra={"fields": {"detected_type": type_label}},
                    )
                    return (True, "", type_label)
            except Exception:
                # A checker should never raise, but be defensive
                continue

    # No signature matched
    hex_preview = contents[:16].hex(" ")
    msg = (
        f"File type could not be identified by magic bytes "
        f"(first 16 bytes: {hex_preview})"
    )
    logger.warning(
        msg,
        extra={"fields": {"hex_preview": hex_preview, "content_length": len(contents)}},
    )
    return (False, msg, "unknown")


def check_content_type(content_type: str, detected_type: str) -> bool:
    """Verify that the declared Content-Type is consistent with the detected type.

    The Content-Type header is attacker-controlled and must never be trusted
    on its own.  This check is a *secondary* consistency gate: if the header
    claims ``image/png`` but magic bytes say JPEG, the upload is rejected.

    Args:
        content_type: The MIME type from the ``Content-Type`` header
            (case-insensitive, parameters ignored).
        detected_type: The type label returned by :func:`check_magic_bytes`.

    Returns:
        ``True`` if the Content-Type is acceptable for the detected type,
        ``False`` otherwise.
    """
    if not content_type or detected_type == "unknown":
        logger.debug(
            "Skipping Content-Type check (content_type=%r, detected_type=%r)",
            content_type,
            detected_type,
        )
        return False

    # Normalise: strip parameters like "; charset=utf-8"
    normalised = content_type.split(";")[0].strip().lower()

    # Must be in the global whitelist first
    if normalised not in _CONTENT_TYPE_WHITELIST:
        logger.warning(
            "Content-Type '%s' is not in the whitelist",
            normalised,
            extra={"fields": {"content_type": normalised}},
        )
        return False

    # Must match the detected type
    acceptable = _TYPE_TO_CONTENT_TYPES.get(detected_type, set())
    if normalised not in acceptable:
        logger.warning(
            "Content-Type '%s' does not match detected type '%s' "
            "(acceptable: %s)",
            normalised,
            detected_type,
            sorted(acceptable),
            extra={
                "fields": {
                    "content_type": normalised,
                    "detected_type": detected_type,
                    "acceptable_types": sorted(acceptable),
                }
            },
        )
        return False

    logger.debug(
        "Content-Type check passed: '%s' matches detected type '%s'",
        normalised,
        detected_type,
    )
    return True


# ---------------------------------------------------------------------------
# Composite validation entry-point
# ---------------------------------------------------------------------------

def validate_upload(
    contents: bytes,
    filename: str,
    content_type: str,
) -> Tuple[bool, str]:
    """Run the full upload validation pipeline.

    This is the single entry-point that routers and services should call.  It
    performs, in order:

    1. **Filename sanitisation** – ensures the name is safe to store/log.
    2. **File size check** – rejects files larger than the configured cap.
    3. **Magic-byte check** – verifies the content matches a known file type.
    4. **Content-Type consistency check** – confirms the declared MIME type is
       plausible given the actual content.

    If any step fails the function returns immediately with ``False`` and a
    descriptive error.  All failures are logged at ``WARNING`` level.

    Args:
        contents: Raw file bytes.
        filename: Original filename supplied by the client.
        content_type: MIME type from the ``Content-Type`` header.

    Returns:
        ``(is_valid, error_message)`` – on success *error_message* is empty.
    """
    # ── Step 1: sanitise filename ─────────────────────────────────────
    safe_name = sanitize_filename(filename)
    if safe_name == "unnamed_upload" and filename:
        # The original name was rejected; let the caller know.
        return (False, "Filename contains disallowed characters or extension")

    # ── Step 2: check file size ──────────────────────────────────────
    size_ok, size_err = check_file_size(contents)
    if not size_ok:
        return (False, size_err)

    # ── Step 3: magic-byte verification ──────────────────────────────
    magic_ok, magic_err, detected_type = check_magic_bytes(contents)
    if not magic_ok:
        return (False, magic_err)

    # ── Step 4: Content-Type consistency ─────────────────────────────
    if not check_content_type(content_type, detected_type):
        return (
            False,
            f"Content-Type '{content_type}' does not match detected file type "
            f"'{detected_type}'",
        )

    # ── Step 5: extension vs detected type consistency ────────────────
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    expected_extensions = _TYPE_TO_EXTENSIONS.get(detected_type, [])
    if ext and ext not in expected_extensions:
        logger.warning(
            "Filename extension '.%s' does not match detected type '%s'",
            ext,
            detected_type,
            extra={
                "fields": {
                    "extension": ext,
                    "detected_type": detected_type,
                    "expected_extensions": expected_extensions,
                }
            },
        )
        # Not a hard rejection – log a warning but allow through, since the
        # magic bytes are the authoritative signal.

    logger.info(
        "Upload validation passed: file='%s' type='%s' size=%d bytes",
        safe_name,
        detected_type,
        len(contents),
        extra={
            "fields": {
                "filename": safe_name,
                "detected_type": detected_type,
                "content_type": content_type,
                "file_size": len(contents),
            }
        },
    )
    return (True, "")
