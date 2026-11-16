-- =============================================================================
-- Demerivo PixelFix — Phase 4 Production Database Migration (PostgreSQL DDL)
-- Subscription Billing (Plans, Subscriptions), Payment Ledger, and Invoices
-- =============================================================================

-- 1. Plans Table
CREATE TABLE IF NOT EXISTS plans (
    id VARCHAR(36) PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(64) NOT NULL,
    description VARCHAR(255) NOT NULL,
    price_paise INTEGER NOT NULL,
    monthly_credits INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    features JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plans_code ON plans (code);

-- Seed Initial Plans
INSERT INTO plans (id, code, name, description, price_paise, monthly_credits, currency, is_active, features)
VALUES 
    ('plan_free', 'free', 'Free Explorer', 'Basic trial tier for testing enhancements', 0, 5, 'INR', TRUE, '["5 Monthly Credits", "Standard CPU Processing", "Upscale 2x & 4x", "Background Removal", "Community Support"]'::jsonb),
    ('plan_starter', 'starter', 'Starter Creator', 'Essential enhancements for hobbyists and creators', 19900, 40, 'INR', TRUE, '["40 Monthly Credits", "Priority CPU Processing", "All AI Enhancement Operations", "Transparent & Solid BG Replacement", "Standard Email Support"]'::jsonb),
    ('plan_pro', 'pro', 'Pro Photographer', 'High-volume enhancement suite for photographers', 49900, 150, 'INR', TRUE, '["150 Monthly Credits", "Turbo Concurrency", "High Resolution Output (Up to 4K)", "All AI Operations + Colorization", "Priority Support"]'::jsonb),
    ('plan_studio', 'studio', 'Studio Enterprise', 'Maximum horsepower for design studios and agencies', 99900, 400, 'INR', TRUE, '["400 Monthly Credits", "Maximum Concurrent Workers", "Ultra HD Output", "All AI Operations", "Dedicated Account Support"]'::jsonb)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    price_paise = EXCLUDED.price_paise,
    monthly_credits = EXCLUDED.monthly_credits,
    features = EXCLUDED.features;

-- 2. Subscriptions Table
CREATE TABLE IF NOT EXISTS subscriptions (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(36) NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    plan_id VARCHAR(36) NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
    plan_code VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    provider VARCHAR(32) NOT NULL DEFAULT 'mock',
    provider_customer_id VARCHAR(128),
    provider_subscription_id VARCHAR(128),
    current_period_start TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    current_period_end TIMESTAMP WITH TIME ZONE NOT NULL,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant_id ON subscriptions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_plan_code ON subscriptions (plan_code);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions (status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_provider_sub ON subscriptions (provider_subscription_id);

-- 3. Payments Table
CREATE TABLE IF NOT EXISTS payments (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    subscription_id VARCHAR(36) REFERENCES subscriptions(id) ON DELETE SET NULL,
    provider VARCHAR(32) NOT NULL DEFAULT 'mock',
    provider_payment_id VARCHAR(128),
    provider_order_id VARCHAR(128),
    amount_paise INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    status VARCHAR(32) NOT NULL DEFAULT 'created',
    idempotency_key VARCHAR(128) UNIQUE,
    error_code VARCHAR(64),
    error_description VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_tenant_id ON payments (tenant_id);
CREATE INDEX IF NOT EXISTS idx_payments_subscription_id ON payments (subscription_id);
CREATE INDEX IF NOT EXISTS idx_payments_provider_payment_id ON payments (provider_payment_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments (status);

-- 4. Invoices Table
CREATE TABLE IF NOT EXISTS invoices (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    subscription_id VARCHAR(36) REFERENCES subscriptions(id) ON DELETE SET NULL,
    payment_id VARCHAR(36) REFERENCES payments(id) ON DELETE SET NULL,
    invoice_number VARCHAR(64) NOT NULL UNIQUE,
    amount_paise INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    status VARCHAR(32) NOT NULL DEFAULT 'paid',
    billing_period_start TIMESTAMP WITH TIME ZONE NOT NULL,
    billing_period_end TIMESTAMP WITH TIME ZONE NOT NULL,
    pdf_url VARCHAR(512),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_invoices_tenant_id ON invoices (tenant_id);
CREATE INDEX IF NOT EXISTS idx_invoices_subscription_id ON invoices (subscription_id);
CREATE INDEX IF NOT EXISTS idx_invoices_number ON invoices (invoice_number);
