-- Database initialization for Medical Handwriting OCR

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Documents table
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    file_name TEXT NOT NULL,
    original_path TEXT NOT NULL,
    page_count INTEGER DEFAULT 1,
    scan_quality_score FLOAT,
    created_at TIMESTAMP DEFAULT NOW(),
    user_id TEXT
);

-- Pages table
CREATE TABLE pages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID REFERENCES documents(id),
    page_number INTEGER NOT NULL,
    image_path TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    ocr_status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW()
);

-- Text regions table (core data for training)
CREATE TABLE text_regions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    page_id UUID REFERENCES pages(id),

    -- Spatial coordinates
    bbox JSONB NOT NULL,  -- {"x1": 120, "y1": 340, "x2": 280, "y2": 380}

    -- Classification
    script_class TEXT CHECK (script_class IN ('arabic', 'latin', 'mixed', 'numeric', 'unknown')),
    region_type TEXT DEFAULT 'word',
    reading_order INTEGER,

    -- OCR results
    predicted_text TEXT,
    confidence FLOAT,
    model_version TEXT DEFAULT 'paddleocr-v1',

    -- Correction
    corrected_text TEXT,
    correction_count INTEGER DEFAULT 0,

    -- Validation
    is_medical_term BOOLEAN DEFAULT FALSE,
    dictionary_match BOOLEAN,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'gold_standard')),

    -- Metadata
    user_id TEXT,
    reviewer_id TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    corrected_at TIMESTAMP,
    reviewed_at TIMESTAMP,

    -- Unique constraint
    CONSTRAINT unique_region_page UNIQUE(page_id, reading_order)
);

CREATE INDEX idx_text_regions_status ON text_regions(status);
CREATE INDEX idx_text_regions_script ON text_regions(script_class);
CREATE INDEX idx_text_regions_created ON text_regions(created_at);
CREATE INDEX idx_text_regions_medical ON text_regions(is_medical_term) WHERE is_medical_term = TRUE;

-- Model versions table
CREATE TABLE model_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    version_name TEXT NOT NULL,
    base_model TEXT,
    trained_on_count INTEGER DEFAULT 0,
    cer_score FLOAT,
    wer_score FLOAT,
    medical_term_accuracy FLOAT,
    training_duration INTEGER,  -- in seconds
    deployed_at TIMESTAMP,
    is_active BOOLEAN DEFAULT FALSE,
    notes TEXT
);

-- Daily statistics table
CREATE TABLE daily_stats (
    date DATE PRIMARY KEY,
    documents_processed INTEGER DEFAULT 0,
    words_extracted INTEGER DEFAULT 0,
    corrections_made INTEGER DEFAULT 0,
    avg_confidence FLOAT,
    avg_correction_time INTEGER  -- in seconds
);

-- Trigger function to update correction stats
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

CREATE TRIGGER trigger_update_stats
AFTER UPDATE ON text_regions
FOR EACH ROW
EXECUTE FUNCTION update_correction_stats();
