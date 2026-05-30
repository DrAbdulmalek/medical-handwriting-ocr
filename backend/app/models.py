from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime

import sqlalchemy
from sqlalchemy import Column, String, Boolean, Integer, DateTime, Float, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

from app.database import Base


# =============================================================================
# Pydantic Models (request / response schemas)
# =============================================================================


class RegionCorrection(BaseModel):
    region_id: UUID
    corrected_text: str
    user_id: Optional[str] = "anonymous"


class RegionResponse(BaseModel):
    id: UUID
    bbox: dict
    predicted_text: str
    confidence: float
    corrected_text: Optional[str] = None
    status: str
    crop_url: Optional[str] = None

    class Config:
        from_attributes = True


class DocumentUpload(BaseModel):
    file_name: str
    page_count: int = 1


class OCRResult(BaseModel):
    document_id: UUID
    page_id: UUID
    regions: List[RegionResponse]
    total_regions: int
    needs_review: int  # Count of low-confidence regions


class StatsResponse(BaseModel):
    total_documents: int
    total_regions: int
    corrected_regions: int
    pending_corrections: int
    avg_confidence: float
    model_version: str


# =============================================================================
# SQLAlchemy ORM Models (database tables)
# =============================================================================


class APIKey(Base):
    """
    Stores API keys used for authenticating external clients.

    The raw key is never stored; only its SHA-256 hash (``key_hash``) is
    persisted.  The ``rate_limit`` column specifies the maximum number of
    requests per minute allowed for this key.
    """

    __tablename__ = "api_keys"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    key_hash = Column(String(64), nullable=False, unique=True, index=True, doc="SHA-256 hash of the raw API key")
    name = Column(String(128), nullable=False, doc="Human-readable key identifier")
    description = Column(String(512), nullable=True, doc="Optional description of the key's purpose")
    is_active = Column(Boolean, nullable=False, default=True, doc="Whether the key is currently enabled")
    rate_limit = Column(Integer, nullable=False, default=100, doc="Max requests per minute for this key")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        doc="Timestamp when the key was created",
    )
    last_used_at = Column(
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp of the most recent authenticated request",
    )
    expires_at = Column(
        DateTime(timezone=True),
        nullable=True,
        doc="Optional expiration timestamp; NULL means no expiration",
    )
    created_by = Column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="UUID of the admin/user who created this key",
    )

    def __repr__(self) -> str:
        return f"<APIKey id={self.id} name={self.name!r} active={self.is_active}>"


class AuditLog(Base):
    """
    Immutable audit trail recording significant actions performed through
    the API (e.g. corrections, uploads, key creation).

    ``details`` is a JSONB column that stores arbitrary structured data
    specific to each action type.
    """

    __tablename__ = "audit_logs"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    action = Column(String(64), nullable=False, index=True, doc="Action name, e.g. 'correction.create', 'document.upload'")
    entity_type = Column(String(64), nullable=False, index=True, doc="Type of entity affected, e.g. 'region', 'document', 'api_key'")
    entity_id = Column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="UUID of the affected entity (if applicable)",
    )
    user_id = Column(
        String(256),
        nullable=True,
        doc="Identifier of the acting user or API key name",
    )
    ip_address = Column(
        String(45),
        nullable=True,
        doc="Client IP address (supports IPv4 and IPv6)",
    )
    details = Column(
        JSONB,
        nullable=True,
        doc="Structured JSON with action-specific metadata",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        index=True,
        doc="Timestamp when the action occurred",
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} action={self.action!r} entity={self.entity_type}/{self.entity_id}>"
