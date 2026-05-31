
-- ============================================================
-- Migration: Add mobile_logs table for centralized logging
-- ============================================================

CREATE TABLE IF NOT EXISTS mobile_logs (
    id VARCHAR(128) PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL,
    device_id VARCHAR(128),
    user_id VARCHAR(256),
    timestamp TIMESTAMP WITH TIME ZONE,
    level VARCHAR(20) NOT NULL CHECK (level IN ('debug', 'info', 'warn', 'error', 'fatal')),
    category VARCHAR(30) NOT NULL,
    message TEXT NOT NULL,
    details JSONB,
    stack_trace TEXT,
    screen VARCHAR(256),
    memory_usage REAL,
    network_status VARCHAR(20),
    app_version VARCHAR(20),
    device_info JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_mobile_logs_session ON mobile_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_mobile_logs_device ON mobile_logs(device_id);
CREATE INDEX IF NOT EXISTS idx_mobile_logs_user ON mobile_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_mobile_logs_level ON mobile_logs(level);
CREATE INDEX IF NOT EXISTS idx_mobile_logs_category ON mobile_logs(category);
CREATE INDEX IF NOT EXISTS idx_mobile_logs_created ON mobile_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_mobile_logs_timestamp ON mobile_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_mobile_logs_app_version ON mobile_logs(app_version);

-- Composite index for analytics
CREATE INDEX IF NOT EXISTS idx_mobile_logs_level_created 
    ON mobile_logs(level, created_at) 
    WHERE level IN ('error', 'fatal', 'warn');

-- Partitioning (optional, for high volume)
-- CREATE TABLE mobile_logs_2024_05 PARTITION OF mobile_logs
--     FOR VALUES FROM ('2024-05-01') TO ('2024-06-01');

-- Verify
SELECT 'mobile_logs' as table_name, COUNT(*) as row_count FROM mobile_logs;
