"""
Authentication system unit tests.

Tests cover:
- JWT token creation, decoding, and validation (security.py)
- RBAC permission checks on User model (auth/models.py)
- Auth router endpoints: register, login, refresh, me, logout (routers/auth.py)

Run: pytest tests/test_auth.py -v
"""

import os
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────
# Pre-import environment configuration
# ─────────────────────────────────────────────────────────────
os.environ.setdefault("API_KEY_AUTH_ENABLED", "false")
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests-min-32-chars!")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

# ─────────────────────────────────────────────────────────────
# Test JWT Security Functions (security.py)
# ─────────────────────────────────────────────────────────────


class TestJWTSecurity:
    """Tests for token creation, decoding, and password hashing."""

    def test_password_hashing_and_verification(self):
        """bcrypt hash/verify round-trip works correctly."""
        from app.auth.security import get_password_hash, verify_password

        plain_password = "SecureP@ssw0rd_2026!"
        hashed = get_password_hash(plain_password)

        assert hashed != plain_password, "Hash must differ from plaintext"
        assert len(hashed) > 20, "Hash should be substantially longer than password"
        assert verify_password(plain_password, hashed), "Correct password must verify"
        assert not verify_password("WrongPassword!", hashed), "Wrong password must not verify"

    def test_password_hash_different_each_time(self):
        """Two hashes of the same password should differ (bcrypt salt)."""
        from app.auth.security import get_password_hash

        password = "SamePassword123"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)

        assert hash1 != hash2, "bcrypt salts should produce different hashes"

    def test_create_access_token_contains_required_claims(self):
        """Access token must contain sub, username, role, type=access, exp, iat."""
        from app.auth.security import create_access_token, decode_token

        user_id = str(uuid.uuid4())
        payload_data = {
            "sub": user_id,
            "username": "dr_test",
            "role": "admin",
        }
        token = create_access_token(payload_data)
        assert isinstance(token, str)
        assert len(token) > 50, "JWT token should be substantial length"

        decoded = decode_token(token)
        assert decoded["sub"] == user_id
        assert decoded["username"] == "dr_test"
        assert decoded["role"] == "admin"
        assert decoded["type"] == "access"
        assert "exp" in decoded
        assert "iat" in decoded

    def test_create_refresh_token_contains_sub_only(self):
        """Refresh token should contain sub and type=refresh but not username/role."""
        from app.auth.security import create_refresh_token, decode_token

        user_id = str(uuid.uuid4())
        token = create_refresh_token({"sub": user_id})
        decoded = decode_token(token)

        assert decoded["sub"] == user_id
        assert decoded["type"] == "refresh"
        assert "username" not in decoded
        assert "role" not in decoded

    def test_decode_access_token_with_refresh_fails(self):
        """Decoding a refresh token with access type expectation raises ValueError."""
        from app.auth.security import create_refresh_token, decode_token

        token = create_refresh_token({"sub": str(uuid.uuid4())})
        with pytest.raises(ValueError, match="token type"):
            decode_token(token, token_type="access")

    def test_decode_expired_token_raises(self):
        """An expired token should raise an expired-jwt exception."""
        from app.auth.security import create_access_token, decode_token
        import jwt

        user_id = str(uuid.uuid4())
        token = create_access_token(
            {"sub": user_id, "username": "test", "role": "guest"},
            expires_delta=timedelta(seconds=-1),
        )

        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(token)

    def test_create_access_token_custom_expiry(self):
        """Tokens respect custom expiry deltas."""
        from app.auth.security import create_access_token, decode_token

        user_id = str(uuid.uuid4())
        # Very short expiry
        token = create_access_token(
            {"sub": user_id, "username": "test", "role": "guest"},
            expires_delta=timedelta(seconds=2),
        )
        decoded = decode_token(token)
        exp = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
        iat = datetime.fromtimestamp(decoded["iat"], tz=timezone.utc)
        delta = (exp - iat).total_seconds()
        assert 1.5 <= delta <= 3.0, f"Expiry delta should be ~2s, got {delta}s"


# ─────────────────────────────────────────────────────────────
# Test RBAC Model Logic (auth/models.py)
# ─────────────────────────────────────────────────────────────


class TestRBACModel:
    """Tests for User/Role/Permission model methods without database."""

    def test_user_role_name_property(self):
        """User.role_name returns the role name when role is set."""
        from app.auth.models import User, Role

        role = Role(
            id=uuid.uuid4(),
            name="admin",
            display_name="Administrator",
            is_system=True,
            created_at=datetime.now(timezone.utc),
        )
        user = User(
            id=uuid.uuid4(),
            username="testadmin",
            email="admin@test.com",
            hashed_password="$2b$12$fakehash",
            role=role,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        assert user.role_name == "admin"
        assert user.role_names == ["admin"]

    def test_user_role_name_none_without_role(self):
        """User.role_name returns None when no role is assigned."""
        from app.auth.models import User

        user = User(
            id=uuid.uuid4(),
            username="norole",
            email="norole@test.com",
            hashed_password="$2b$12$fakehash",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        assert user.role_name is None
        assert user.role_names == []

    def test_user_permissions_list_from_role(self):
        """User.permissions_list extracts permission names from role."""
        from app.auth.models import User, Role, Permission

        perm1 = Permission(
            id=uuid.uuid4(),
            name="upload:documents",
            display_name="Upload Documents",
            resource_type="documents",
            action="upload",
            created_at=datetime.now(timezone.utc),
        )
        perm2 = Permission(
            id=uuid.uuid4(),
            name="view:reports",
            display_name="View Reports",
            resource_type="reports",
            action="view",
            created_at=datetime.now(timezone.utc),
        )
        role = Role(
            id=uuid.uuid4(),
            name="doctor",
            display_name="Doctor",
            permissions=[perm1, perm2],
            created_at=datetime.now(timezone.utc),
        )
        user = User(
            id=uuid.uuid4(),
            username="dr_test",
            email="dr@test.com",
            hashed_password="$2b$12$fakehash",
            role=role,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        perms = user.permissions_list
        assert "upload:documents" in perms
        assert "view:reports" in perms
        assert len(perms) == 2

    def test_user_has_permission(self):
        """User.has_permission checks correctly."""
        from app.auth.models import User, Role, Permission

        perm = Permission(
            id=uuid.uuid4(),
            name="correct:ocr",
            display_name="Correct OCR",
            resource_type="ocr",
            action="correct",
            created_at=datetime.now(timezone.utc),
        )
        role = Role(
            id=uuid.uuid4(),
            name="reviewer",
            display_name="Reviewer",
            permissions=[perm],
            created_at=datetime.now(timezone.utc),
        )
        user = User(
            id=uuid.uuid4(),
            username="reviewer1",
            email="reviewer@test.com",
            hashed_password="$2b$12$fakehash",
            role=role,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        assert user.has_permission("correct:ocr") is True
        assert user.has_permission("manage:users") is False

    def test_user_has_any_role(self):
        """User.has_any_role checks against role names."""
        from app.auth.models import User, Role

        role = Role(
            id=uuid.uuid4(),
            name="technician",
            display_name="Technician",
            created_at=datetime.now(timezone.utc),
        )
        user = User(
            id=uuid.uuid4(),
            username="tech1",
            email="tech@test.com",
            hashed_password="$2b$12$fakehash",
            role=role,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        assert user.has_any_role("technician") is True
        assert user.has_any_role("admin", "technician") is True
        assert user.has_any_role("admin", "doctor") is False

    def test_user_repr(self):
        """User __repr__ provides useful debugging info."""
        from app.auth.models import User

        user = User(
            id=uuid.uuid4(),
            username="repr_test",
            email="repr@test.com",
            hashed_password="$2b$12$fakehash",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        repr_str = repr(user)
        assert "repr_test" in repr_str
        assert "active=True" in repr_str


# ─────────────────────────────────────────────────────────────
# Test Auth Router Endpoints (routers/auth.py)
# ─────────────────────────────────────────────────────────────


class TestAuthRouterEndpoints:
    """
    Tests for authentication API endpoints.

    Uses httpx.AsyncClient with mocked database to test request/response
    handling without needing a real database or external services.
    """

    @pytest.fixture(autouse=True)
    def _setup_mocks(self):
        """Mock external dependencies before importing app."""
        import sys
        from unittest.mock import MagicMock

        # Mock storage, OCR engine, celery, redis before importing app
        for mod_name in ["app.storage", "app.ocr_engine", "app.celery_app"]:
            if mod_name not in sys.modules:
                sys.modules[mod_name] = MagicMock()

    @pytest.fixture()
    async def auth_client(self):
        """Create an httpx.AsyncClient with mocked dependencies."""
        import sys
        from unittest.mock import MagicMock, patch

        import httpx

        # Ensure all external service modules are mocked
        for mod_name in ["app.storage", "app.ocr_engine", "app.celery_app"]:
            if mod_name not in sys.modules:
                sys.modules[mod_name] = MagicMock()

        from app.main import app
        from app.database import get_db, Base

        mock_db = MagicMock()

        with (
            patch.object(Base.metadata, "create_all"),
            patch("app.database.SessionLocal") as mock_sl,
            patch("app.middleware.api_key_auth.APIKey_AUTH_ENABLED", False),
        ):
            # Make health check DB call succeed
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

    async def test_register_missing_fields_returns_422(self, auth_client):
        """POST /api/auth/register without required fields returns 422."""
        response = await auth_client.post(
            "/api/auth/register",
            json={"username": "ab"},  # Too short, missing email & password
        )
        assert response.status_code == 422

    async def test_register_empty_body_returns_422(self, auth_client):
        """POST /api/auth/register with empty body returns 422."""
        response = await auth_client.post("/api/auth/register", json={})
        assert response.status_code == 422

    async def test_login_missing_fields_returns_422(self, auth_client):
        """POST /api/auth/login without credentials returns 422."""
        response = await auth_client.post("/api/auth/login", json={})
        assert response.status_code == 422

    async def test_login_invalid_credentials_returns_401(self, auth_client):
        """POST /api/auth/login with non-existent user returns 401."""
        # Mock DB to return None (user not found)
        from app.database import get_db
        from unittest.mock import MagicMock

        mock_db = MagicMock()
        mock_db.scalar.return_value = None
        auth_client._transport.app.dependency_overrides[get_db] = lambda: mock_db

        response = await auth_client.post(
            "/api/auth/login",
            json={"username": "nonexistent", "password": "password123"},
        )
        assert response.status_code == 401

    async def test_refresh_missing_token_returns_422(self, auth_client):
        """POST /api/auth/refresh without token returns 422."""
        response = await auth_client.post("/api/auth/refresh", json={})
        assert response.status_code == 422

    async def test_refresh_invalid_token_returns_401(self, auth_client):
        """POST /api/auth/refresh with invalid token returns 401."""
        response = await auth_client.post(
            "/api/auth/refresh",
            json={"refresh_token": "invalid.jwt.token"},
        )
        assert response.status_code == 401

    async def test_me_without_token_returns_403(self, auth_client):
        """GET /api/auth/me without auth token returns 401/403."""
        response = await auth_client.get("/api/auth/me")
        assert response.status_code in (401, 403)

    async def test_logout_without_token_returns_403(self, auth_client):
        """POST /api/auth/logout without auth token returns 401/403."""
        response = await auth_client.post("/api/auth/logout")
        assert response.status_code in (401, 403)

    async def test_users_without_admin_auth_returns_403(self, auth_client):
        """GET /api/auth/users without admin token returns 401/403."""
        response = await auth_client.get("/api/auth/users")
        assert response.status_code in (401, 403)

    async def test_register_success_with_valid_data(self, auth_client):
        """POST /api/auth/register with valid data creates user (201)."""
        from app.auth.models import User, Role
        from app.database import get_db
        from unittest.mock import MagicMock
        import uuid

        mock_db = MagicMock()

        # Simulate: username not taken, email not taken, guest role exists
        # First _get_user_by_username_or_email call returns None
        # email_check returns None
        # guest role lookup succeeds
        guest_role = Role(
            id=uuid.uuid4(),
            name="guest",
            display_name="Guest",
            is_system=True,
            created_at=datetime.now(timezone.utc),
        )
        new_user = User(
            id=uuid.uuid4(),
            username="newuser",
            email="new@test.com",
            hashed_password="$2b$12$fakehash",
            full_name="New User",
            role=guest_role,
            is_active=True,
            is_verified=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        mock_db.scalar.side_effect = [None, None, guest_role]
        mock_db.refresh.side_effect = lambda obj: None

        def mock_add_fn(obj):
            obj.id = new_user.id

        mock_db.add = mock_add_fn
        auth_client._transport.app.dependency_overrides[get_db] = lambda: mock_db

        response = await auth_client.post(
            "/api/auth/register",
            json={
                "username": "newuser",
                "email": "new@test.com",
                "password": "ValidPass123!",
                "full_name": "New User",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["username"] == "newuser"
        assert body["email"] == "new@test.com"
        assert body["is_active"] is True

    async def test_register_duplicate_username_returns_409(self, auth_client):
        """POST /api/auth/register with existing username returns 409."""
        from app.auth.models import User, Role
        from app.database import get_db
        from unittest.mock import MagicMock
        import uuid

        mock_db = MagicMock()

        existing_user = User(
            id=uuid.uuid4(),
            username="existinguser",
            email="existing@test.com",
            hashed_password="$2b$12$fakehash",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        # _get_user_by_username_or_email matches by username
        mock_db.scalar.side_effect = [existing_user]
        auth_client._transport.app.dependency_overrides[get_db] = lambda: mock_db

        response = await auth_client.post(
            "/api/auth/register",
            json={
                "username": "existinguser",
                "email": "different@test.com",
                "password": "ValidPass123!",
            },
        )
        assert response.status_code == 409
        assert "already taken" in response.json()["detail"].lower()
