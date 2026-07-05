"""
JWT token management and password hashing utilities.

Provides production-ready functions for:
    - Password hashing and verification (bcrypt via passlib)
    - Access token creation and validation (short-lived, 30 min)
    - Refresh token creation and validation (long-lived, 7 days)
    - Graceful handling of expired, invalid, and malformed tokens

Configuration:
    All secrets and expiry durations are read from ``app.config.settings``.

Token payload structure:
    {
        "sub": "<user_uuid>",        // Subject — the authenticated user
        "username": "<username>",     // Human-readable identifier
        "role": "<role_name>",        // Role for quick access in downstream logic
        "type": "access" | "refresh", // Token type discriminator
        "exp": <unix_timestamp>,     // Expiry time (set by PyJWT)
        "iat": <unix_timestamp>,     // Issued-at time (set by PyJWT)
    }
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from uuid import UUID

import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Password Hashing — bcrypt with passlib
# ---------------------------------------------------------------------------

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plaintext password against a bcrypt hash.

    Args:
        plain_password:  The raw password string provided by the user.
        hashed_password: The bcrypt hash stored in the database.

    Returns:
        ``True`` if the password matches, ``False`` otherwise.

    Raises:
        ValueError: If ``plain_password`` is empty or ``hashed_password`` is malformed.
    """
    if not plain_password:
        logger.warning("verify_password called with empty plain_password")
        return False
    try:
        return _pwd_context.verify(plain_password, hashed_password)
    except Exception as exc:
        logger.error("Password verification failed with error: %s", exc)
        return False


def get_password_hash(password: str) -> str:
    """
    Hash a plaintext password using bcrypt.

    Args:
        password: The raw password string to hash.

    Returns:
        A bcrypt hash string suitable for storage in the database.
    """
    return _pwd_context.hash(password)


# ---------------------------------------------------------------------------
# JWT Token Management
# ---------------------------------------------------------------------------

# Token type constants
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def _now_utc() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def create_access_token(data: Dict) -> str:
    """
    Create a short-lived JWT access token.

    The token expires after ``settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES`` minutes
    (default: 30).  The ``type`` claim is set to ``"access"``.

    Args:
        data: A dictionary containing at minimum ``sub`` (user UUID) and
              optionally ``username`` and ``role``.  Should **not** contain
              ``exp``, ``iat``, or ``type`` — those are set automatically.

    Returns:
        An encoded JWT string.

    Example:
        >>> token = create_access_token({
        ...     "sub": "a1b2c3d4-...",
        ...     "username": "drsmith",
        ...     "role": "doctor",
        ... })
    """
    to_encode = data.copy()
    expire = _now_utc() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({
        "type": TOKEN_TYPE_ACCESS,
        "exp": expire,
        "iat": _now_utc(),
    })
    encoded = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    logger.debug("Access token created for sub=%s, expires=%s", data.get("sub"), expire.isoformat())
    return encoded


def create_refresh_token(data: Dict) -> str:
    """
    Create a long-lived JWT refresh token.

    The token expires after ``settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS`` days
    (default: 7).  The ``type`` claim is set to ``"refresh"`` and the ``sub``
    claim is the only required field.

    Args:
        data: A dictionary containing at minimum ``sub`` (user UUID).
              Should **not** contain ``exp``, ``iat``, or ``type``.

    Returns:
        An encoded JWT string.
    """
    to_encode = data.copy()
    expire = _now_utc() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({
        "type": TOKEN_TYPE_REFRESH,
        "exp": expire,
        "iat": _now_utc(),
    })
    encoded = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    logger.debug("Refresh token created for sub=%s, expires=%s", data.get("sub"), expire.isoformat())
    return encoded


def decode_token(token: str, token_type: str = "access") -> Dict:
    """
    Decode and validate a JWT token.

    Validates:
        1. Cryptographic signature against ``settings.SECRET_KEY``.
        2. Token has not expired (``exp`` claim).
        3. Token type matches the expected ``token_type`` (``"access"`` or ``"refresh"``).

    Args:
        token:      The encoded JWT string.
        token_type: Expected token type — ``"access"`` or ``"refresh"``.

    Returns:
        A dictionary containing the decoded token payload.

    Raises:
        jwt.ExpiredSignatureError:  Token has expired.
        jwt.InvalidTokenError:     Token is malformed, signature is invalid, or type mismatch.
        ValueError:                Token type does not match the expected type.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token has expired (type=%s)", token_type)
        raise
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid JWT token (type=%s): %s", token_type, exc)
        raise

    # Verify token type claim
    if payload.get("type") != token_type:
        logger.warning(
            "JWT token type mismatch: expected=%s, got=%s",
            token_type,
            payload.get("type"),
        )
        raise ValueError(f"Expected token type '{token_type}', got '{payload.get('type')}'")

    return payload


# ---------------------------------------------------------------------------
# Pydantic Schemas (Token Data / Response)
# ---------------------------------------------------------------------------


class TokenData(BaseModel):
    """
    Internal representation of a decoded JWT token's payload.

    Used by ``get_current_user`` and other dependencies to pass validated
    token data through the request pipeline.
    """

    sub: str = Field(..., description="User UUID (the 'subject' claim)")
    username: Optional[str] = Field(None, description="Username from the token")
    role: Optional[str] = Field(None, description="Role name from the token")
    token_type: str = Field(..., description="Token type: 'access' or 'refresh'")


class TokenResponse(BaseModel):
    """
    Standard response returned after successful login or token refresh.

    Includes both access and refresh tokens along with metadata.
    """

    access_token: str = Field(..., description="Short-lived JWT access token (30 min)")
    refresh_token: str = Field(..., description="Long-lived JWT refresh token (7 days)")
    token_type: str = Field(default="bearer", description="Token type, always 'bearer'")
    expires_in: int = Field(
        ...,
        description="Access token lifetime in seconds",
        examples=[1800],
    )

    class Config:
        json_schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIs...",
                "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
                "token_type": "bearer",
                "expires_in": 1800,
            }
        }
