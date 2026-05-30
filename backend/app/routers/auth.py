"""
Authentication API router for the Medical Data Analysis Platform.

Provides RESTful endpoints for user registration, login, token refresh,
profile retrieval, user management (admin-only), and role assignment.

All endpoints are prefixed with ``/api/auth`` and tagged as
``"authentication"`` for OpenAPI documentation grouping.

Protection model:
    - Public:   register, login, refresh
    - Protected: me, logout  (require valid access token)
    - Admin:    users list, role change, deactivate  (require "admin" role)

Error handling:
    - Comprehensive validation with structured error responses
    - Detailed logging for security events (failed logins, permission changes)
    - Consistent HTTP status codes (400, 401, 403, 404, 409, 422)
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.auth.models import User, Role, Permission
from app.auth.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    decode_token,
    TokenResponse,
)
from app.auth.dependencies import get_current_user, require_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/auth", tags=["authentication"])

_bearer_scheme = HTTPBearer(auto_error=False)


# ===========================================================================
# Pydantic Request / Response Schemas
# ===========================================================================


class RegisterRequest(BaseModel):
    """Schema for user registration requests."""

    username: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Unique login name (3–64 chars, alphanumeric, underscores, hyphens)",
    )
    email: str = Field(
        ...,
        min_length=5,
        max_length=256,
        description="Unique email address for identification and notifications",
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Password (min 8 characters, should include letters and numbers)",
    )
    full_name: Optional[str] = Field(
        None,
        max_length=256,
        description="Full display name, e.g. 'Dr. Sarah Johnson'",
    )
    specialty: Optional[str] = Field(
        None,
        max_length=128,
        description="Medical specialty, e.g. 'Radiology'",
    )
    institution: Optional[str] = Field(
        None,
        max_length=256,
        description="Hospital or clinic affiliation",
    )


class LoginRequest(BaseModel):
    """Schema for user login requests."""

    username: str = Field(
        ...,
        description="Username or email to authenticate with",
    )
    password: str = Field(
        ...,
        description="Account password",
    )


class RefreshRequest(BaseModel):
    """Schema for token refresh requests."""

    refresh_token: str = Field(
        ...,
        description="The long-lived refresh token obtained at login",
    )


class UserResponse(BaseModel):
    """Schema for user profile responses (excludes sensitive data)."""

    id: UUID
    username: str
    email: str
    full_name: Optional[str] = None
    specialty: Optional[str] = None
    institution: Optional[str] = None
    is_active: bool
    is_verified: bool
    last_login: Optional[datetime] = None
    role_id: Optional[UUID] = None
    role_name: Optional[str] = Field(None, alias="role_name")
    permissions: List[str] = Field(default_factory=list, description="Permission names from role")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class RoleAssignmentRequest(BaseModel):
    """Schema for changing a user's role (admin only)."""

    role_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Name of the role to assign, e.g. 'doctor', 'reviewer'",
    )


class MessageResponse(BaseModel):
    """Generic success/failure response."""

    message: str
    detail: Optional[str] = None


# ===========================================================================
# Helper: get user by username or email
# ===========================================================================


def _get_user_by_username_or_email(db: Session, identifier: str) -> Optional[User]:
    """Look up a user by username or email (supports login with either)."""
    stmt = select(User).where(
        (User.username == identifier) | (User.email == identifier)
    )
    return db.scalar(stmt)


# ===========================================================================
# Public Endpoints
# ===========================================================================


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Create a new user account. The user is assigned the default 'guest' role.",
    responses={
        201: {"description": "User created successfully"},
        409: {"description": "Username or email already exists"},
        422: {"description": "Validation error"},
    },
)
async def register(
    request: RegisterRequest,
    db: Session = Depends(get_db),
):
    """
    Register a new user account.

    Creates the user with a bcrypt-hashed password and assigns the
    default ``guest`` role.  The account is created with ``is_verified=False``
    (email verification would be a production addition).
    """
    # ── Check for existing username or email ────────────────────
    existing_user = _get_user_by_username_or_email(db, request.username)
    if existing_user is not None:
        if existing_user.username == request.username:
            logger.warning("Registration failed: username '%s' already taken", request.username)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Username '{request.username}' is already taken.",
            )
        # existing_user matched by email
        logger.warning("Registration failed: email '%s' already registered", request.email)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{request.email}' is already registered.",
        )

    # Also check email independently (in case username != email)
    email_check = db.scalar(select(User).where(User.email == request.email))
    if email_check is not None:
        logger.warning("Registration failed: email '%s' already registered", request.email)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{request.email}' is already registered.",
        )

    # ── Find default role ──────────────────────────────────────
    guest_role = db.scalar(select(Role).where(Role.name == "guest"))
    if guest_role is None:
        logger.error("Default 'guest' role not found in database. Registration aborted.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="System misconfiguration: default role not found. Contact administrator.",
        )

    # ── Create user ────────────────────────────────────────────
    user = User(
        username=request.username,
        email=request.email,
        hashed_password=get_password_hash(request.password),
        full_name=request.full_name,
        specialty=request.specialty,
        institution=request.institution,
        is_active=True,
        is_verified=False,
        role_id=guest_role.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info(
        "New user registered: id=%s username=%s email=%s",
        user.id,
        user.username,
        user.email,
    )

    return _build_user_response(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and obtain tokens",
    description="Login with username/email and password. Returns access + refresh tokens.",
    responses={
        200: {"description": "Authentication successful, tokens returned"},
        401: {"description": "Invalid credentials"},
        403: {"description": "Account deactivated"},
    },
)
async def login(
    request: LoginRequest,
    db: Session = Depends(get_db),
):
    """
    Authenticate a user and return JWT access + refresh tokens.

    Accepts either a username or email as the ``username`` field.
    On success, the user's ``last_login`` timestamp is updated.
    """
    user = _get_user_by_username_or_email(db, request.username)

    # ── User not found ─────────────────────────────────────────
    if user is None:
        logger.warning("Login attempt for unknown user: %s", request.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Account deactivated ─────────────────────────────────────
    if not user.is_active:
        logger.warning("Login attempt for deactivated user: %s", request.username)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact an administrator.",
        )

    # ── Verify password ─────────────────────────────────────────
    if not verify_password(request.password, user.hashed_password):
        logger.warning("Failed login (wrong password) for user: %s", request.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Update last login ──────────────────────────────────────
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    # ── Generate tokens ───────────────────────────────────────
    token_data = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role_name or "guest",
    }
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token({"sub": str(user.id)})

    logger.info("Successful login: user=%s id=%s", user.username, user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
    description="Exchange a valid refresh token for a new access + refresh token pair.",
    responses={
        200: {"description": "Tokens refreshed successfully"},
        401: {"description": "Invalid or expired refresh token"},
    },
)
async def refresh_tokens(
    request: RefreshRequest,
    db: Session = Depends(get_db),
):
    """
    Refresh JWT tokens using a valid refresh token.

    Validates the refresh token, checks the user still exists and is active,
    then issues a new access + refresh token pair.
    """
    try:
        payload = decode_token(request.refresh_token, token_type="refresh")
    except ValueError as exc:
        logger.warning("Token refresh failed — type mismatch: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as exc:
        logger.warning("Token refresh failed — invalid token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing user identifier.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_uuid = UUID(user_id_str)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user identifier in refresh token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.scalar(select(User).where(User.id == user_uuid))
    if user is None:
        logger.warning("Refresh token references non-existent user: %s", user_id_str)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        logger.warning("Refresh attempt for deactivated user: %s", user.username)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated.",
        )

    # ── Issue new tokens ───────────────────────────────────────
    token_data = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role_name or "guest",
    }
    access_token = create_access_token(token_data)
    new_refresh_token = create_refresh_token({"sub": str(user.id)})

    logger.info("Tokens refreshed for user: %s", user.username)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# ===========================================================================
# Protected Endpoints
# ===========================================================================


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
    description="Return the profile of the currently authenticated user.",
    responses={
        200: {"description": "User profile returned"},
        401: {"description": "Authentication required"},
    },
)
async def get_me(user: User = Depends(get_current_user)):
    """Return the authenticated user's profile including role and permissions."""
    return _build_user_response(user)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Logout (invalidate session hint)",
    description=(
        "Accepts a valid access token and returns a success message. "
        "Production implementations should add token blacklisting."
    ),
    responses={
        200: {"description": "Logout successful"},
        401: {"description": "Authentication required"},
    },
)
async def logout(user: User = Depends(get_current_user)):
    """
    Process a logout request.

    In the current implementation, this simply confirms the token was valid.
    A production system would add the token to a blacklist (e.g. Redis set)
    for immediate revocation.
    """
    logger.info("User logged out: %s (id=%s)", user.username, user.id)
    return MessageResponse(
        message="Logged out successfully.",
        detail="Token blacklisting would be enabled in production deployments.",
    )


# ===========================================================================
# Admin Endpoints
# ===========================================================================


@router.get(
    "/users",
    response_model=List[UserResponse],
    summary="List all users (admin only)",
    description="Return a paginated list of all registered users. Requires admin role.",
    responses={
        200: {"description": "List of users returned"},
        401: {"description": "Authentication required"},
        403: {"description": "Admin role required"},
    },
)
async def list_users(
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
):
    """
    List all registered users.

    Only accessible to users with the ``admin`` role.
    Supports pagination via ``skip`` and ``limit`` query parameters.
    """
    if limit > 500:
        limit = 500

    stmt = (
        select(User)
        .options(joinedload(User.role).joinedload(Role.permissions))
        .order_by(User.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    users = db.scalars(stmt).unique().all()

    logger.info(
        "Admin %s listed %d users (skip=%d, limit=%d)",
        user.username,
        len(users),
        skip,
        limit,
    )

    return [_build_user_response(u) for u in users]


@router.put(
    "/users/{user_id}/role",
    response_model=UserResponse,
    summary="Change user role (admin only)",
    description="Assign a new role to a user. Requires admin role.",
    responses={
        200: {"description": "Role updated successfully"},
        401: {"description": "Authentication required"},
        403: {"description": "Admin role required"},
        404: {"description": "User or role not found"},
    },
)
async def change_user_role(
    user_id: UUID,
    request: RoleAssignmentRequest,
    admin_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Change a user's assigned role.

    Only accessible to users with the ``admin`` role.
    Validates that the target role exists before assignment.
    """
    # ── Find target user ──────────────────────────────────────
    target_user = db.scalar(select(User).where(User.id == user_id))
    if target_user is None:
        logger.warning(
            "Admin %s attempted to change role for non-existent user: %s",
            admin_user.username,
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id '{user_id}' not found.",
        )

    # ── Find target role ──────────────────────────────────────
    target_role = db.scalar(select(Role).where(Role.name == request.role_name))
    if target_role is None:
        logger.warning(
            "Admin %s attempted to assign non-existent role '%s' to user %s",
            admin_user.username,
            request.role_name,
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{request.role_name}' not found.",
        )

    old_role_name = target_user.role_name
    target_user.role_id = target_role.id
    db.commit()
    db.refresh(target_user)

    logger.info(
        "Admin %s changed role for user %s: %s → %s",
        admin_user.username,
        target_user.username,
        old_role_name,
        request.role_name,
    )

    return _build_user_response(target_user)


@router.post(
    "/users/{user_id}/deactivate",
    response_model=UserResponse,
    summary="Deactivate a user account (admin only)",
    description="Deactivate a user's account, preventing login. Requires admin role.",
    responses={
        200: {"description": "User deactivated successfully"},
        401: {"description": "Authentication required"},
        403: {"description": "Admin role required"},
        404: {"description": "User not found"},
    },
)
async def deactivate_user(
    user_id: UUID,
    admin_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Deactivate a user account.

    Only accessible to users with the ``admin`` role.
    Prevents the target user from logging in until reactivated.
    """
    # ── Find target user ──────────────────────────────────────
    target_user = db.scalar(select(User).where(User.id == user_id))
    if target_user is None:
        logger.warning(
            "Admin %s attempted to deactivate non-existent user: %s",
            admin_user.username,
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id '{user_id}' not found.",
        )

    # ── Prevent deactivating self ──────────────────────────────
    if target_user.id == admin_user.id:
        logger.warning("Admin %s attempted to deactivate themselves", admin_user.username)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    if not target_user.is_active:
        logger.info(
            "Admin %s: user %s is already deactivated",
            admin_user.username,
            target_user.username,
        )
        return _build_user_response(target_user)

    target_user.is_active = False
    db.commit()
    db.refresh(target_user)

    logger.info(
        "Admin %s deactivated user %s (id=%s)",
        admin_user.username,
        target_user.username,
        user_id,
    )

    return _build_user_response(target_user)


# ===========================================================================
# Helpers
# ===========================================================================


def _build_user_response(user: User) -> UserResponse:
    """
    Construct a ``UserResponse`` from a ``User`` ORM object.

    Ensures the role relationship is loaded and extracts the flat
    permissions list from the user's role.
    """
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        specialty=user.specialty,
        institution=user.institution,
        is_active=user.is_active,
        is_verified=user.is_verified,
        last_login=user.last_login,
        role_id=user.role_id,
        role_name=user.role_name,
        permissions=user.permissions_list,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
