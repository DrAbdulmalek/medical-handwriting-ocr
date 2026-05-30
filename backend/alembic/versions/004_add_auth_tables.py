"""Add RBAC authentication tables: roles, permissions, users, role_permissions

Creates the complete Role-Based Access Control (RBAC) schema:
- roles:               Named role buckets (admin, doctor, reviewer, technician, guest)
- permissions:          Granular permission grants (resource:action format)
- role_permissions:      Many-to-many association table (roles ↔ permissions)
- users:                User accounts with role assignment

Seeds default roles with appropriate permissions:
- admin:       All permissions (full platform access)
- doctor:      upload:documents, correct:ocr, approve:gold_standard, view:reports, view:audit_logs
- reviewer:    correct:ocr, approve:gold_standard, view:reports, view:audit_logs
- technician:  upload:documents, correct:ocr
- guest:       view:reports

Revision ID: 004
Revises: 003
Create Date: 2025-01-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ====================================================================
    # 1. roles table
    # ====================================================================
    op.create_table(
        "roles",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "name",
            sa.String(64),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(128),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.String(512),
            nullable=True,
        ),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_roles_name", "roles", ["name"], unique=True)

    # ====================================================================
    # 2. permissions table
    # ====================================================================
    op.create_table(
        "permissions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "name",
            sa.String(128),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(256),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.String(512),
            nullable=True,
        ),
        sa.Column(
            "resource_type",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "action",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_permissions_name", "permissions", ["name"], unique=True)
    op.create_index("idx_permissions_resource", "permissions", ["resource_type"])
    op.create_index("idx_permissions_action", "permissions", ["action"])

    # ====================================================================
    # 3. role_permissions association table
    # ====================================================================
    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id",
            UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "permission_id",
            UUID(as_uuid=True),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index("idx_rp_role_id", "role_permissions", ["role_id"])
    op.create_index("idx_rp_permission_id", "role_permissions", ["permission_id"])

    # ====================================================================
    # 4. users table
    # ====================================================================
    op.create_table(
        "users",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "username",
            sa.String(64),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "email",
            sa.String(256),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "hashed_password",
            sa.String(128),
            nullable=False,
        ),
        sa.Column(
            "full_name",
            sa.String(256),
            nullable=True,
        ),
        sa.Column(
            "specialty",
            sa.String(128),
            nullable=True,
        ),
        sa.Column(
            "institution",
            sa.String(256),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "last_login",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "role_id",
            UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_users_username", "users", ["username"], unique=True)
    op.create_index("idx_users_email", "users", ["email"], unique=True)
    op.create_index("idx_users_role_id", "users", ["role_id"])

    # ====================================================================
    # 5. Seed default permissions
    # ====================================================================
    permissions_data = [
        # (name, display_name, description, resource_type, action)
        (
            "upload:documents",
            "Upload Documents",
            "Upload medical documents and images for OCR processing",
            "documents",
            "upload",
        ),
        (
            "correct:ocr",
            "Correct OCR Results",
            "Submit corrections to OCR-recognized text regions",
            "ocr",
            "correct",
        ),
        (
            "approve:gold_standard",
            "Approve Gold Standard",
            "Approve corrections as gold standard training data",
            "gold_standard",
            "approve",
        ),
        (
            "view:reports",
            "View Reports",
            "Access analytics reports and dashboards",
            "reports",
            "view",
        ),
        (
            "export:data",
            "Export Data",
            "Export processed data in various formats",
            "data",
            "export",
        ),
        (
            "manage:users",
            "Manage Users",
            "Create, edit, and deactivate user accounts",
            "users",
            "manage",
        ),
        (
            "manage:dictionaries",
            "Manage Dictionaries",
            "Create and edit medical term dictionaries",
            "dictionaries",
            "manage",
        ),
        (
            "train:models",
            "Train Models",
            "Trigger model training jobs and fine-tuning",
            "models",
            "train",
        ),
        (
            "deploy:models",
            "Deploy Models",
            "Deploy and promote model versions to production",
            "models",
            "deploy",
        ),
        (
            "view:audit_logs",
            "View Audit Logs",
            "Access the audit trail for compliance and debugging",
            "audit_logs",
            "view",
        ),
    ]

    op.bulk_insert(
        op.get_bind().execute(
            sa.text("SELECT 1 WHERE FALSE")
        ),
        [],
    )

    # Use raw SQL for reliable UUID generation in seed data
    perm_sql = """
        INSERT INTO permissions (id, name, display_name, description, resource_type, action)
        VALUES
            (gen_random_uuid(), 'upload:documents',    'Upload Documents',         'Upload medical documents and images for OCR processing',        'documents',    'upload'),
            (gen_random_uuid(), 'correct:ocr',          'Correct OCR Results',      'Submit corrections to OCR-recognized text regions',            'ocr',          'correct'),
            (gen_random_uuid(), 'approve:gold_standard', 'Approve Gold Standard',     'Approve corrections as gold standard training data',           'gold_standard','approve'),
            (gen_random_uuid(), 'view:reports',         'View Reports',              'Access analytics reports and dashboards',                       'reports',      'view'),
            (gen_random_uuid(), 'export:data',           'Export Data',               'Export processed data in various formats',                     'data',         'export'),
            (gen_random_uuid(), 'manage:users',         'Manage Users',              'Create, edit, and deactivate user accounts',                   'users',        'manage'),
            (gen_random_uuid(), 'manage:dictionaries',   'Manage Dictionaries',       'Create and edit medical term dictionaries',                     'dictionaries', 'manage'),
            (gen_random_uuid(), 'train:models',         'Train Models',              'Trigger model training jobs and fine-tuning',                   'models',       'train'),
            (gen_random_uuid(), 'deploy:models',        'Deploy Models',             'Deploy and promote model versions to production',              'models',       'deploy'),
            (gen_random_uuid(), 'view:audit_logs',      'View Audit Logs',           'Access the audit trail for compliance and debugging',           'audit_logs',   'view')
        ON CONFLICT (name) DO NOTHING;
    """
    op.execute(perm_sql)

    # ====================================================================
    # 6. Seed default roles
    # ====================================================================
    roles_sql = """
        INSERT INTO roles (id, name, display_name, description, is_system)
        VALUES
            (gen_random_uuid(), 'admin',      'Administrator',  'Full platform access with all permissions',                TRUE),
            (gen_random_uuid(), 'doctor',     'Doctor',         'Clinical workflow: upload, correct, approve, view reports', TRUE),
            (gen_random_uuid(), 'reviewer',   'Reviewer',       'Review and approve OCR corrections and gold standards',    TRUE),
            (gen_random_uuid(), 'technician', 'Technician',     'Upload documents and submit basic corrections',            TRUE),
            (gen_random_uuid(), 'guest',      'Guest',          'Read-only access to reports and public data',               TRUE)
        ON CONFLICT (name) DO NOTHING;
    """
    op.execute(roles_sql)

    # ====================================================================
    # 7. Assign permissions to roles
    # ====================================================================
    # Admin gets ALL permissions
    admin_perms_sql = """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE r.name = 'admin'
        ON CONFLICT DO NOTHING;
    """
    op.execute(admin_perms_sql)

    # Doctor: upload:documents, correct:ocr, approve:gold_standard, view:reports, view:audit_logs
    doctor_perms_sql = """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE r.name = 'doctor'
          AND p.name IN ('upload:documents', 'correct:ocr', 'approve:gold_standard', 'view:reports', 'view:audit_logs')
        ON CONFLICT DO NOTHING;
    """
    op.execute(doctor_perms_sql)

    # Reviewer: correct:ocr, approve:gold_standard, view:reports, view:audit_logs
    reviewer_perms_sql = """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE r.name = 'reviewer'
          AND p.name IN ('correct:ocr', 'approve:gold_standard', 'view:reports', 'view:audit_logs')
        ON CONFLICT DO NOTHING;
    """
    op.execute(reviewer_perms_sql)

    # Technician: upload:documents, correct:ocr
    technician_perms_sql = """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE r.name = 'technician'
          AND p.name IN ('upload:documents', 'correct:ocr')
        ON CONFLICT DO NOTHING;
    """
    op.execute(technician_perms_sql)

    # Guest: view:reports
    guest_perms_sql = """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE r.name = 'guest'
          AND p.name IN ('view:reports')
        ON CONFLICT DO NOTHING;
    """
    op.execute(guest_perms_sql)


def downgrade() -> None:
    """Drop all RBAC tables in reverse dependency order."""
    # Drop association table first
    op.drop_index("idx_rp_permission_id", table_name="role_permissions")
    op.drop_index("idx_rp_role_id", table_name="role_permissions")
    op.drop_table("role_permissions")

    # Drop users table
    op.drop_index("idx_users_role_id", table_name="users")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_index("idx_users_username", table_name="users")
    op.drop_table("users")

    # Drop permissions table
    op.drop_index("idx_permissions_action", table_name="permissions")
    op.drop_index("idx_permissions_resource", table_name="permissions")
    op.drop_index("idx_permissions_name", table_name="permissions")
    op.drop_table("permissions")

    # Drop roles table
    op.drop_index("idx_roles_name", table_name="roles")
    op.drop_table("roles")
