"""
Authentication and authorization package for the Medical Data Analysis Platform.

Provides:
    - User, Role, Permission ORM models (app.auth.models)
    - JWT token management and password hashing (app.auth.security)
    - FastAPI dependency injection for auth flows (app.auth.dependencies)

Usage:
    from app.auth.models import User, Role, Permission
    from app.auth.security import verify_password, create_access_token
    from app.auth.dependencies import get_current_user, require_role, require_permission
"""

from app.auth.models import User, Role, Permission  # noqa: F401
from app.auth.security import (  # noqa: F401
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    decode_token,
    TokenData,
    TokenResponse,
)
from app.auth.dependencies import (  # noqa: F401
    get_current_user,
    require_role,
    require_permission,
)

__all__ = [
    # ORM Models
    "User",
    "Role",
    "Permission",
    # Security utilities
    "verify_password",
    "get_password_hash",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "TokenData",
    "TokenResponse",
    # FastAPI dependencies
    "get_current_user",
    "require_role",
    "require_permission",
]
