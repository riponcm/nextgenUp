-- =============================================================================
-- Demerivo PixelFix — Phase 5 & 6 Production Database Migration (PostgreSQL DDL)
-- Platform Administration, Immutable Audit Trail, Cryptographic Email Tokens & Worker Heartbeats
-- =============================================================================

-- 1. Upgrade Users Table with Platform Admin Flag
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_users_platform_admin ON users (is_platform_admin);

-- 2. Phase 5 Admin Audit Logs Table (Immutable Audit Trail)
CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id VARCHAR(36) PRIMARY KEY,
    actor_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    actor_email VARCHAR(255) NOT NULL,
    action VARCHAR(64) NOT NULL,
    target_type VARCHAR(64),
    target_id VARCHAR(128),
    reason TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'success',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_action_created ON admin_audit_logs (action, created_at);
CREATE INDEX IF NOT EXISTS idx_admin_audit_target ON admin_audit_logs (target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_actor_email ON admin_audit_logs (actor_email);

-- Revoke UPDATE and DELETE permissions from standard application user for true DB-level immutability
-- (Can be executed by DB superuser in production):
-- REVOKE UPDATE, DELETE ON admin_audit_logs FROM demerivo_user;

-- 3. Phase 6 Email Verification Tokens Table (Cryptographic Single-Use Tokens)
CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_email_token_lookup ON email_verification_tokens (email, used_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_email_token_hash ON email_verification_tokens (token_hash);
CREATE INDEX IF NOT EXISTS idx_email_token_user_id ON email_verification_tokens (user_id);

-- 4. Phase 6 Worker Heartbeats Table (Infrastructure Monitoring)
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id VARCHAR(64) PRIMARY KEY,
    hostname VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    active_jobs_count INTEGER NOT NULL DEFAULT 0,
    last_heartbeat TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_last ON worker_heartbeats (last_heartbeat);
CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_status ON worker_heartbeats (status);
