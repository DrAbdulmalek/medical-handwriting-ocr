"""
User, Role, and Permission ORM models for RBAC authentication.

Defines the database schema for user accounts, roles, and fine-grained
permissions with a many-to-many relationship between roles and permissions.

Relationships:
    User  ←──N:1──→  Role  ←──N:M──→  Permission

Tables:
    - users:        Individual user accounts
    - roles:        Named role buckets (e.g. admin, doctor, technician)
    - permissions:  Granular action grants (e.g. upload:documents)
    - role_permissions: Association table linking roles ↔ permissions
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    DateTime,
    Float,
    Text,
    ForeignKey,
    Table,
    text,
    relationship,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

from app.database import Base

logger = logging.getLogger(__name__)


# =============================================================================
# Association Table: role_permissions
# =============================================================================

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column(
        "role_id",
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
        doc="Foreign key to roles table",
    ),
    Column(
        "permission_id",
        PG_UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
        doc="Foreign key to permissions table",
    ),
)


# =============================================================================
# Role Model
# =============================================================================


class Role(Base):
    """
    Represents a named role that groups a set of permissions.

    System roles (``is_system=True``) are seeded by migrations and cannot
    be deleted through the API.  Default system roles include:
        - admin       — Full platform access
        - doctor      — Clinical workflow permissions
        - reviewer    — Review and approve corrections
        - technician  — Upload and basic processing
        - guest       — Read-only access
    """

    __tablename__ = "roles"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        doc="Unique role identifier (UUID v4)",
    )
    name = Column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        doc="Machine-readable role name, e.g. 'admin', 'doctor'",
    )
    display_name = Column(
        String(128),
        nullable=False,
        doc="Human-readable label, e.g. 'Administrator'",
    )
    description = Column(
        String(512),
        nullable=True,
        doc="Detailed description of the role's purpose and scope",
    )
    is_system = Column(
        Boolean,
        nullable=False,
        default=False,
        doc="If True, this role cannot be deleted via the API",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        doc="Timestamp when the role was created",
    )

    # ── Relationships ──────────────────────────────────────────

    permissions = relationship(
        "Permission",
        secondary=role_permissions,
        back_populates="roles",
        lazy="selectin",
        doc="Permissions granted to this role",
    )
    users = relationship(
        "User",
        back_populates="role",
        lazy="selectin",
        doc="Users assigned to this role",
    )

    def __repr__(self) -> str:
        return (
            f"<Role id={self.id} name={self.name!r} "
            f"display={self.display_name!r} system={self.is_system}>"
        )


# =============================================================================
# Permission Model
# =============================================================================


class Permission(Base):
    """
    Represents a single, fine-grained permission grant.

    Permissions follow the ``resource:action`` naming convention:
        - ``upload:documents``    — Upload new medical documents
        - ``correct:ocr``         — Submit OCR corrections
        - ``approve:gold_standard``— Approve corrections as gold standard
        - ``view:reports``        — View analytics reports
        - ``export:data``         — Export processed data
        - ``manage:users``        — Create / edit / deactivate users
        - ``manage:dictionaries`` — Manage medical term dictionaries
        - ``train:models``        — Trigger model training jobs
        - ``deploy:models``       — Deploy model versions
        - ``view:audit_logs``     — Access the audit trail
    """

    __tablename__ = "permissions"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        doc="Unique permission identifier (UUID v4)",
    )
    name = Column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
        doc="Machine-readable permission name, e.g. 'upload:documents'",
    )
    display_name = Column(
        String(256),
        nullable=False,
        doc="Human-readable label, e.g. 'Upload Documents'",
    )
    description = Column(
        String(512),
        nullable=True,
        doc="Detailed description of what this permission controls",
    )
    resource_type = Column(
        String(64),
        nullable=False,
        index=True,
        doc="Resource category, e.g. 'documents', 'users', 'models'",
    )
    action = Column(
        String(64),
        nullable=False,
        index=True,
        doc="Action allowed, e.g. 'upload', 'view', 'manage'",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        doc="Timestamp when the permission was created",
    )

    # ── Relationships ──────────────────────────────────────────

    roles = relationship(
        "Role",
        secondary=role_permissions,
        back_populates="permissions",
        lazy="selectin",
        doc="Roles that include this permission",
    )

    def __repr__(self) -> str:
        return (
            f"<Permission id={self.id} name={self.name!r} "
            f"resource={self.resource_type!r} action={self.action!r}>"
        )


# =============================================================================
# User Model
# =============================================================================


class User(Base):
    """
    Represents an individual user account in the platform.

    Each user is assigned exactly one role (simple RBAC).  Permissions
    are derived transitively through the role's ``permissions`` relationship.

    Attributes:
        id:              UUID primary key
        username:        Unique login identifier
        email:            Unique email address
        hashed_password: bcrypt hash of the user's password
        full_name:       Display name (e.g. "Dr. John Smith")
        specialty:       Medical specialty (e.g. "Cardiology")
        institution:      Hospital or clinic affiliation
        is_active:       Whether the account is currently enabled
        is_verified:     Whether the email has been verified
        last_login:      Timestamp of the most recent successful login
        role_id:         Foreign key to the user's assigned role
        created_at:      Account creation timestamp
        updated_at:      Last profile modification timestamp
    """

    __tablename__ = "users"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        doc="Unique user identifier (UUID v4)",
    )
    username = Column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        doc="Unique login name (3–64 characters)",
    )
    email = Column(
        String(256),
        unique=True,
        nullable=False,
        index=True,
        doc="Unique email address used for identification and notifications",
    )
    hashed_password = Column(
        String(128),
        nullable=False,
        doc="bcrypt hash of the user's password (never store plaintext)",
    )
    full_name = Column(
        String(256),
        nullable=True,
        doc="Full display name, e.g. 'Dr. Sarah Johnson'",
    )
    specialty = Column(
        String(128),
        nullable=True,
        doc="Medical specialty, e.g. 'Radiology', 'Cardiology'",
    )
    institution = Column(
        String(256),
        nullable=True,
        doc="Hospital / clinic / organization affiliation",
    )
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        doc="Whether the account is currently active and can log in",
    )
    is_verified = Column(
        Boolean,
        nullable=False,
        default=False,
        doc="Whether the user's email has been verified",
    )
    last_login = Column(
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp of the most recent successful authentication",
    )
    role_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Foreign key to the user's assigned role (NULL = no role)",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        doc="Account creation timestamp",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
        doc="Last profile update timestamp",
    )

    # ── Relationships ──────────────────────────────────────────

    role = relationship(
        "Role",
        back_populates="users",
        lazy="joined",
        doc="The role assigned to this user",
    )

    # ── Computed Properties ────────────────────────────────────

    @property
    def role_name(self) -> Optional[str]:
        """Return the user's role name, or ``None`` if no role is assigned."""
        return self.role.name if self.role else None

    @property
    def role_names(self) -> List[str]:
        """Return a list containing the user's role name(s). Kept as a list for forward compatibility."""
        if self.role:
            return [self.role.name]
        return []

    @property
    def permissions_list(self) -> List[str]:
        """Return a flat list of permission names granted through the user's role."""
        if self.role and self.role.permissions:
            return [p.name for p in self.role.permissions]
        return []

    def has_permission(self, permission_name: str) -> bool:
        """Check if the user has a specific permission by name."""
        return permission_name in self.permissions_list

    def has_any_role(self, *role_names: str) -> bool:
        """Check if the user has any of the specified role names."""
        return any(rn in self.role_names for rn in role_names)

    def __repr__(self) -> str:
        return (
            f"<User id={self.id} username={self.username!r} "
            f"email={self.email!r} active={self.is_active} "
            f"role={self.role_name!r}>"
        )
