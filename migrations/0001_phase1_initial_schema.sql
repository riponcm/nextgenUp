-- =============================================================================
-- Demerivo PixelFix — Phase 1 Initial Database Migration (PostgreSQL DDL)
-- Core Asynchronous Processing Jobs Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS jobs (
    id VARCHAR(36) PRIMARY KEY,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    operation_type VARCHAR(32) NOT NULL,
    input_key VARCHAR(512) NOT NULL,
    output_key VARCHAR(512),
    progress INTEGER NOT NULL DEFAULT 0,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs (created_at);
