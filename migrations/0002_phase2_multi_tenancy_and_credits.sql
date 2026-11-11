-- =============================================================================
-- Demerivo PixelFix — Phase 2 Production Database Migration (PostgreSQL DDL)
-- Multi-Tenancy (Users, Tenants, Memberships) & Credit Accounting (Wallets, Transactions)
-- =============================================================================

-- 1. Users Table
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(36) PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(100),
    avatar_url VARCHAR(512),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users (created_at);

-- 2. Tenants Table (Workspaces)
CREATE TABLE IF NOT EXISTS tenants (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(120) NOT NULL UNIQUE,
    is_personal BOOLEAN NOT NULL DEFAULT TRUE,
    owner_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants (slug);
CREATE INDEX IF NOT EXISTS idx_tenants_owner_id ON tenants (owner_id);

-- 3. Tenant Members Table
CREATE TABLE IF NOT EXISTS tenant_members (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(32) NOT NULL DEFAULT 'member',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT uq_tenant_member UNIQUE (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_members_tenant ON tenant_members (tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_members_user ON tenant_members (user_id);
CREATE INDEX IF NOT EXISTS idx_tenant_member_role ON tenant_members (tenant_id, role);

-- 4. Credit Wallets Table
CREATE TABLE IF NOT EXISTS wallets (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(36) NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT chk_wallet_balance_non_negative CHECK (balance >= 0)
);

CREATE INDEX IF NOT EXISTS idx_wallets_tenant_id ON wallets (tenant_id);

-- 5. Credit Transactions Table (Immutable Audit Ledger)
CREATE TABLE IF NOT EXISTS credit_transactions (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    wallet_id VARCHAR(36) NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
    user_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    job_id VARCHAR(36) REFERENCES jobs(id) ON DELETE SET NULL,
    transaction_type VARCHAR(32) NOT NULL,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    idempotency_key VARCHAR(128) UNIQUE,
    description VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_credit_tx_tenant_id ON credit_transactions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_credit_tx_wallet_id ON credit_transactions (wallet_id);
CREATE INDEX IF NOT EXISTS idx_credit_tx_user_id ON credit_transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_credit_tx_job_id ON credit_transactions (job_id);
CREATE INDEX IF NOT EXISTS idx_credit_tx_idempotency ON credit_transactions (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_credit_tx_tenant_created ON credit_transactions (tenant_id, created_at);

-- 6. Update Jobs Table with Tenant Scoping and Credit Cost
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(36) REFERENCES tenants(id) ON DELETE CASCADE;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_id VARCHAR(36) REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS credit_cost INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128) UNIQUE;

CREATE INDEX IF NOT EXISTS idx_jobs_tenant_id ON jobs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs (user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_tenant_created ON jobs (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_tenant_status ON jobs (tenant_id, status);
