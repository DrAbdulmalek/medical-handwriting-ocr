
-- ============================================================
-- Migration: Add mobile sync tables to PostgreSQL
-- Run: psql -d medical_ocr -f mobile_migration.sql
-- ============================================================

-- 1. Mobile sync log table (tracks all sync operations)
CREATE TABLE IF NOT EXISTS mobile_sync_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id VARCHAR(128) NOT NULL,
    direction VARCHAR(10) NOT NULL CHECK (direction IN ('push', 'pull')),
    accepted_count INTEGER DEFAULT 0,
    rejected_count INTEGER DEFAULT 0,
    sync_token VARCHAR(128) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mobile_sync_device ON mobile_sync_logs(device_id);
CREATE INDEX IF NOT EXISTS idx_mobile_sync_token ON mobile_sync_logs(sync_token);
CREATE INDEX IF NOT EXISTS idx_mobile_sync_created ON mobile_sync_logs(created_at);

-- 2. Orphan corrections (mobile corrections before full document sync)
CREATE TABLE IF NOT EXISTS mobile_orphan_corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id VARCHAR(128) NOT NULL,
    local_region_id VARCHAR(128) NOT NULL,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    predicted_text TEXT NOT NULL,
    corrected_text TEXT NOT NULL,
    corrected_at TIMESTAMP WITH TIME ZONE,
    user_id VARCHAR(256) DEFAULT 'anonymous',
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orphan_device ON mobile_orphan_corrections(device_id);
CREATE INDEX IF NOT EXISTS idx_orphan_local ON mobile_orphan_corrections(local_region_id);
CREATE INDEX IF NOT EXISTS idx_orphan_doc ON mobile_orphan_corrections(document_id);
CREATE INDEX IF NOT EXISTS idx_orphan_resolved ON mobile_orphan_corrections(resolved);

-- 3. Add updated_at to text_regions (for incremental sync)
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'text_regions' AND column_name = 'updated_at'
    ) THEN
        ALTER TABLE text_regions ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();
    END IF;
END $$;

-- 4. Trigger: auto-update updated_at on text_regions
CREATE OR REPLACE FUNCTION update_text_regions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_text_regions_updated_at ON text_regions;
CREATE TRIGGER trg_text_regions_updated_at
    BEFORE UPDATE ON text_regions
    FOR EACH ROW
    EXECUTE FUNCTION update_text_regions_updated_at();

-- 5. Add device_id to text_regions
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'text_regions' AND column_name = 'device_id'
    ) THEN
        ALTER TABLE text_regions ADD COLUMN device_id VARCHAR(128);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_regions_device ON text_regions(device_id);

-- 6. Add sync_status to text_regions (for tracking server sync state)
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'text_regions' AND column_name = 'sync_status'
    ) THEN
        ALTER TABLE text_regions ADD COLUMN sync_status VARCHAR(20) DEFAULT 'pending' 
            CHECK (sync_status IN ('pending', 'synced', 'failed'));
    END IF;
END $$;

-- 7. Add last_sync_at to documents
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'documents' AND column_name = 'last_sync_at'
    ) THEN
        ALTER TABLE documents ADD COLUMN last_sync_at TIMESTAMP WITH TIME ZONE;
    END IF;
END $$;

-- 8. Verify all mobile tables exist
SELECT 'mobile_sync_logs' as table_name, COUNT(*) as row_count FROM mobile_sync_logs
UNION ALL
SELECT 'mobile_orphan_corrections', COUNT(*) FROM mobile_orphan_corrections
UNION ALL
SELECT 'text_regions', COUNT(*) FROM text_regions;
