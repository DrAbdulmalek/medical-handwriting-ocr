# Task 1+2: RBAC + JWT Authentication System

## Agent: main-orchestrator
## Status: COMPLETED

## Files Created/Modified

### New Files Created (6 files + 1 work record)
1. **backend/app/auth/__init__.py** — Package exports for all auth components
2. **backend/app/auth/models.py** — User, Role, Permission ORM models with:
   - `role_permissions` association table (many-to-many)
   - `Role` model with `is_system` flag for non-deletable default roles
   - `Permission` model with `resource_type:action` naming convention
   - `User` model with `role_names`, `permissions_list`, `has_permission()`, `has_any_role()` properties
   - Full `__repr__` for all models, comprehensive docstrings

3. **backend/app/auth/security.py** — JWT + password hashing:
   - bcrypt password hashing via passlib (`verify_password`, `get_password_hash`)
   - JWT access token (30 min expiry) via PyJWT (`create_access_token`)
   - JWT refresh token (7 day expiry) (`create_refresh_token`)
   - Token decoding with type validation (`decode_token`)
   - Pydantic schemas: `TokenData`, `TokenResponse`

4. **backend/app/auth/dependencies.py** — FastAPI dependency injection:
   - `get_current_user` — HTTPBearer token extraction → User ORM object
   - `require_role(*role_names)` — Role-based access control factory
   - `require_permission(permission_name)` — Permission-based access control factory
   - Proper 401/403 HTTPException handling with logging

5. **backend/app/routers/auth.py** — 8 API endpoints:
   - `POST /api/auth/register` — Register with default "guest" role
   - `POST /api/auth/login` — Login with username/email + password → TokenResponse
   - `POST /api/auth/refresh` — Refresh access token
   - `GET /api/auth/me` — Get current user profile (protected)
   - `POST /api/auth/logout` — Logout (protected)
   - `GET /api/auth/users` — List all users (admin only, paginated)
   - `PUT /api/auth/users/{user_id}/role` — Change user role (admin only)
   - `POST /api/auth/users/{user_id}/deactivate` — Deactivate user (admin only)

6. **backend/alembic/versions/004_add_auth_tables.py** — Migration:
   - Creates roles, permissions, role_permissions, users tables
   - Seeds 5 default roles: admin, doctor, reviewer, technician, guest
   - Seeds 10 default permissions: upload:documents, correct:ocr, approve:gold_standard, view:reports, export:data, manage:users, manage:dictionaries, train:models, deploy:models, view:audit_logs
   - Assigns permissions: admin gets all, doctor gets upload/correct/approve/view/audit, reviewer gets correct/approve/view/audit, technician gets upload/correct, guest gets view:reports

### Modified Files (3 files)
1. **backend/app/config.py** — Added JWT settings:
   - `SECRET_KEY`, `JWT_ALGORITHM`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_REFRESH_TOKEN_EXPIRE_DAYS`

2. **backend/requirements.txt** — Replaced `python-jose` with `PyJWT==2.8.0`

3. **backend/app/main.py** — Registered auth router:
   - `from app.routers import auth as auth_router`
   - `app.include_router(auth_router.router, tags=["authentication"])`
   - Added "authentication" to openapi_tags

## Import Convention
All files use `from app.xxx` pattern (NOT `from backend.app.xxx`), consistent with the existing codebase.

## Patterns Followed
- SQLAlchemy ORM with `Base = declarative_base()`, `PG_UUID`, `gen_random_uuid()`
- Pydantic BaseModel for request/response schemas with `from_attributes = True`
- `logger = logging.getLogger(__name__)` in every module
- HTTPException with structured error responses
- Alembic migration pattern matching existing 001/002/003 migrations
