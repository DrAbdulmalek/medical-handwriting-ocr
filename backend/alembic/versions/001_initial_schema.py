"""Initial schema: documents, pages, text_regions, model_versions, daily_stats

Creates the core database tables for the Medical Handwriting OCR application:
- documents: uploaded document metadata
- pages: individual page images from documents
- text_regions: OCR-detected text regions with corrections and validation
- model_versions: model training/deployment tracking
- daily_stats: daily aggregation metrics

Revision ID: 001
Revises:
Create Date: 2025-01-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, DATE

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable uuid-ossp extension for UUID generation
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ----------------------------------------------------------------
    # documents table
    # ----------------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("original_path", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), server_default="1"),
        sa.Column("scan_quality_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("user_id", sa.Text(), nullable=True),
    )

    # ----------------------------------------------------------------
    # pages table
    # ----------------------------------------------------------------
    op.create_table(
        "pages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("ocr_status", sa.Text(), server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ----------------------------------------------------------------
    # text_regions table
    # ----------------------------------------------------------------
    op.create_table(
        "text_regions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("page_id", UUID(as_uuid=True), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=True),
        # Spatial coordinates
        sa.Column("bbox", JSONB(), nullable=False),
        # Classification
        sa.Column(
            "script_class",
            sa.Text(),
            sa.CheckConstraint(
                "script_class IN ('arabic', 'latin', 'mixed', 'numeric', 'unknown')",
                name="ck_text_regions_script_class",
            ),
        ),
        sa.Column("region_type", sa.Text(), server_default="word"),
        sa.Column("reading_order", sa.Integer(), nullable=True),
        # OCR results
        sa.Column("predicted_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model_version", sa.Text(), server_default="paddleocr-v1"),
        # Correction
        sa.Column("corrected_text", sa.Text(), nullable=True),
        sa.Column("correction_count", sa.Integer(), server_default="0"),
        # Validation
        sa.Column("is_medical_term", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("dictionary_match", sa.Boolean(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            sa.CheckConstraint(
                "status IN ('pending', 'approved', 'rejected', 'gold_standard')",
                name="ck_text_regions_status",
            ),
            server_default="pending",
        ),
        # Metadata
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("reviewer_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        # Unique constraint
        sa.UniqueConstraint("page_id", "reading_order", name="uq_text_regions_page_reading_order"),
    )

    # Indexes on text_regions
    op.create_index("idx_text_regions_status", "text_regions", ["status"])
    op.create_index("idx_text_regions_script", "text_regions", ["script_class"])
    op.create_index("idx_text_regions_created", "text_regions", ["created_at"])
    op.create_index(
        "idx_text_regions_medical",
        "text_regions",
        ["is_medical_term"],
        postgresql_where=sa.text("is_medical_term = TRUE"),
    )

    # ----------------------------------------------------------------
    # model_versions table
    # ----------------------------------------------------------------
    op.create_table(
        "model_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("version_name", sa.Text(), nullable=False),
        sa.Column("base_model", sa.Text(), nullable=True),
        sa.Column("trained_on_count", sa.Integer(), server_default="0"),
        sa.Column("cer_score", sa.Float(), nullable=True),
        sa.Column("wer_score", sa.Float(), nullable=True),
        sa.Column("medical_term_accuracy", sa.Float(), nullable=True),
        sa.Column("training_duration", sa.Integer(), nullable=True),  # in seconds
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    # ----------------------------------------------------------------
    # daily_stats table
    # ----------------------------------------------------------------
    op.create_table(
        "daily_stats",
        sa.Column("date", DATE(), primary_key=True),
        sa.Column("documents_processed", sa.Integer(), server_default="0"),
        sa.Column("words_extracted", sa.Integer(), server_default="0"),
        sa.Column("corrections_made", sa.Integer(), server_default="0"),
        sa.Column("avg_confidence", sa.Float(), nullable=True),
        sa.Column("avg_correction_time", sa.Integer(), nullable=True),  # in seconds
    )

    # ----------------------------------------------------------------
    # Trigger: auto-increment correction_count and reset status on correction
    # ----------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION update_correction_stats()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.corrected_text IS NOT NULL AND OLD.corrected_text IS NULL THEN
                UPDATE text_regions
                SET correction_count = correction_count + 1,
                    status = 'pending',
                    corrected_at = NOW()
                WHERE id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trigger_update_stats
        AFTER UPDATE ON text_regions
        FOR EACH ROW
        EXECUTE FUNCTION update_correction_stats();
    """)


def downgrade() -> None:
    # Drop trigger and function first
    op.execute("DROP TRIGGER IF EXISTS trigger_update_stats ON text_regions")
    op.execute("DROP FUNCTION IF EXISTS update_correction_stats()")

    # Drop tables in reverse dependency order
    op.drop_table("daily_stats")
    op.drop_table("model_versions")
    op.drop_index("idx_text_regions_medical", table_name="text_regions")
    op.drop_index("idx_text_regions_created", table_name="text_regions")
    op.drop_index("idx_text_regions_script", table_name="text_regions")
    op.drop_index("idx_text_regions_status", table_name="text_regions")
    op.drop_table("text_regions")
    op.drop_table("pages")
    op.drop_table("documents")

    # Drop uuid-ossp extension
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
