"""
FastAPI dependency injection utilities for authentication and authorization.

Provides reusable dependencies that can be injected into route handlers:

    - ``get_current_user``:   Extracts and validates a JWT Bearer token, returns the
                               corresponding ``User`` ORM object from the database.
    - ``require_role``:        Dependency factory that checks the current user's role
                               against one or more allowed role names.
    - ``require_permission``:  Dependency factory that checks the current user's
                               permissions (derived via their role).

Usage in route handlers:

    from fastapi import APIRouter, Depends
    from app.auth.dependencies import get_current_user, require_role, require_permission
    from app.auth.models import User

    router = APIRouter()

    @router.get("/profile")
    async def get_profile(user: User = Depends(get_current_user)):
        return {"username": user.username}

    @router.get("/admin/dashboard")
    async def admin_dashboard(user: User = Depends(require_role("admin"))):
        return {"message": "Admin access granted"}

    @router.post("/documents")
    async def upload_document(user: User = Depends(require_permission("upload:documents"))):
        return {"message": "Upload permission verified"}
"""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.security import decode_token, TokenData
from app.auth.models import User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bearer token extractor (shared across all dependencies)
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Core Dependency: get_current_user
# ---------------------------------------------------------------------------


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Extract, validate, and resolve a JWT Bearer token to a ``User`` ORM object.

    Flow:
        1. Extract the ``Authorization: Bearer <token>`` header.
        2. Decode and validate the JWT (signature, expiry, type="access").
        3. Look up the user by UUID in the database.
        4. Verify the user exists and is active.
        5. Return the ``User`` object.

    Args:
        credentials: Automatically injected by FastAPI's ``HTTPBearer``.
        db:           Automatically injected by ``get_db`` session dependency.

    Returns:
        The authenticated ``User`` ORM instance.

    Raises:
        HTTPException(401): Token missing, invalid, expired, or user not found / inactive.
    """
    # ── Step 1: Extract bearer token ───────────────────────────
    if credentials is None or not credentials.credentials:
        logger.warning("Authentication attempt with missing Bearer token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a valid Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # ── Step 2: Decode and validate JWT ────────────────────────
    try:
        payload = decode_token(token, token_type="access")
    except ValueError as exc:
        logger.warning("JWT type mismatch: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as exc:
        # Covers ExpiredSignatureError, InvalidTokenError, etc.
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Step 3: Extract subject (user UUID) ────────────────────
    user_id_str: Optional[str] = payload.get("sub")
    if not user_id_str:
        logger.warning("JWT token missing 'sub' claim")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing the 'sub' (user ID) claim.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Step 4: Look up user in database ───────────────────────
    try:
        user_uuid = UUID(user_id_str)
    except (ValueError, TypeError):
        logger.warning("JWT 'sub' claim is not a valid UUID: %s", user_id_str)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user identifier in token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    stmt = select(User).where(User.id == user_uuid)
    user = db.scalar(stmt)

    if user is None:
        logger.warning("JWT references non-existent user: %s", user_id_str)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Step 5: Check account status ──────────────────────────
    if not user.is_active:
        logger.warning("Inactive user attempted authentication: %s", user.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is deactivated. Contact an administrator.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


# ---------------------------------------------------------------------------
# Dependency Factory: require_role
# ---------------------------------------------------------------------------


def require_role(*role_names: str):
    """
    Dependency factory that enforces role-based access control.

    Returns a FastAPI dependency that first authenticates the user via
    ``get_current_user``, then verifies the user's role is in the
    allowed list.

    Args:
        *role_names: One or more role names that are permitted access.
                     Example: ``require_role("admin", "reviewer")``

    Returns:
        A dependency function that yields an authenticated ``User`` with
        the required role.

    Raises:
        HTTPException(403): User's role is not in the allowed list.

    Example:
        ::

            @router.delete("/users/{user_id}")
            async def delete_user(
                user: User = Depends(require_role("admin")),
            ):
                ...
    """

    async def _role_checker(
        user: User = Depends(get_current_user),
    ) -> User:
        if not user.has_any_role(*role_names):
            logger.warning(
                "Role-based access denied: user=%s has roles=%s, required=%s",
                user.username,
                user.role_names,
                list(role_names),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role(s): {', '.join(role_names)}.",
            )
        return user

    return _role_checker


# ---------------------------------------------------------------------------
# Dependency Factory: require_permission
# ---------------------------------------------------------------------------


def require_permission(permission_name: str):
    """
    Dependency factory that enforces permission-based access control.

    Returns a FastAPI dependency that first authenticates the user via
    ``get_current_user``, then checks whether the user's role grants
    the specified permission.

    Args:
        permission_name: The permission to check, e.g. ``"upload:documents"``.

    Returns:
        A dependency function that yields an authenticated ``User`` with
        the required permission.

    Raises:
        HTTPException(403): User's role does not include the required permission.

    Example:
        ::

            @router.post("/train")
            async def train_model(
                user: User = Depends(require_permission("train:models")),
            ):
                ...
    """

    async def _permission_checker(
        user: User = Depends(get_current_user),
    ) -> User:
        if not user.has_permission(permission_name):
            logger.warning(
                "Permission-based access denied: user=%s missing permission=%s",
                user.username,
                permission_name,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied. Required: '{permission_name}'.",
            )
        return user

    return _permission_checker
