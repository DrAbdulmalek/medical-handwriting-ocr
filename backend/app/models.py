from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime

import sqlalchemy
from sqlalchemy import Column, String, Boolean, Integer, DateTime, Float, Text, Date, ForeignKey, text, relationship
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


class Document(Base):
    __tablename__ = "documents"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"))
    file_name = Column(Text, nullable=False)
    original_path = Column(Text, nullable=False)
    page_count = Column(Integer, default=1)
    scan_quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
    user_id = Column(Text, nullable=True)
    pages = relationship("Page", back_populates="document")


class Page(Base):
    __tablename__ = "pages"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"))
    document_id = Column(PG_UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    page_number = Column(Integer, nullable=False)
    image_path = Column(Text, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    ocr_status = Column(Text, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
    document = relationship("Document", back_populates="pages")
    text_regions = relationship("TextRegion", back_populates="page")


class TextRegion(Base):
    __tablename__ = "text_regions"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"))
    page_id = Column(PG_UUID(as_uuid=True), ForeignKey("pages.id"), nullable=False)
    bbox = Column(JSONB, nullable=False)
    script_class = Column(Text, nullable=True)
    region_type = Column(Text, default="word")
    reading_order = Column(Integer, nullable=True)
    predicted_text = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    model_version = Column(Text, default="paddleocr-v1")
    corrected_text = Column(Text, nullable=True)
    correction_count = Column(Integer, default=0)
    is_medical_term = Column(Boolean, default=False)
    dictionary_match = Column(Boolean, nullable=True)
    status = Column(Text, default="pending")
    user_id = Column(Text, nullable=True)
    reviewer_id = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
    corrected_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    page = relationship("Page", back_populates="text_regions")


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"))
    version_name = Column(Text, nullable=False)
    base_model = Column(Text, nullable=True)
    trained_on_count = Column(Integer, default=0)
    cer_score = Column(Float, nullable=True)
    wer_score = Column(Float, nullable=True)
    medical_term_accuracy = Column(Float, nullable=True)
    training_duration = Column(Integer, nullable=True)
    deployed_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)


class DailyStats(Base):
    __tablename__ = "daily_stats"
    date = Column(Date, primary_key=True)
    documents_processed = Column(Integer, default=0)
    words_extracted = Column(Integer, default=0)
    corrections_made = Column(Integer, default=0)
    avg_confidence = Column(Float, nullable=True)
    avg_correction_time = Column(Integer, nullable=True)
