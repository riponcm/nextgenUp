-- =============================================================================
-- Demerivo PixelFix — Phase 6 Database Migration (PostgreSQL DDL)
-- Enforce Job-Level Credit Refund Uniqueness for Exactly-Once Accounting
-- =============================================================================

-- 1. Inspect and safely drop legacy constraint or index uq_credit_tx_job_type if present.
-- Must handle BOTH cases:
--   a) Table constraint backed by an index on credit_transactions: ALTER TABLE credit_transactions DROP CONSTRAINT ...
--   b) Standalone unique index on credit_transactions: DROP INDEX ...
-- NEVER drop an unrelated index on another table just because it has the same name.
DO $$
BEGIN
    -- Check if uq_credit_tx_job_type is a table constraint on credit_transactions in current_schema()
    IF EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema()
          AND t.relname = 'credit_transactions'
          AND c.conname = 'uq_credit_tx_job_type'
    ) THEN
        EXECUTE format('ALTER TABLE %I.credit_transactions DROP CONSTRAINT uq_credit_tx_job_type', current_schema());
    ELSIF EXISTS (
        SELECT 1 FROM pg_index ix
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema()
          AND t.relname = 'credit_transactions'
          AND i.relname = 'uq_credit_tx_job_type'
    ) THEN
        EXECUTE format('DROP INDEX IF EXISTS %I.uq_credit_tx_job_type', current_schema());
    END IF;
END $$;

-- 2. Inspect existing uq_credit_tx_job_refund definition structurally.
-- Check: correct schema, correct table ('credit_transactions'), unique property,
-- exactly one indexed key column ('job_id'), and valid partial predicate.
-- If an existing index/constraint is structurally invalid, safely drop it so it can be recreated cleanly.
DO $$
DECLARE
    is_valid BOOLEAN := FALSE;
    rec RECORD;
    pred TEXT;
BEGIN
    -- Check if index or constraint named uq_credit_tx_job_refund exists in current_schema()
    SELECT
        t.relname AS table_name,
        ix.indisunique AS is_unique,
        ix.indnkeyatts AS num_key_cols,
        ARRAY(
            SELECT a.attname
            FROM pg_attribute a
            WHERE a.attrelid = t.oid
              AND a.attnum = ANY(ix.indkey[0:ix.indnkeyatts-1])
        ) AS indexed_columns,
        pg_get_expr(ix.indpred, ix.indrelid) AS predicate_expr
    INTO rec
    FROM pg_index ix
    JOIN pg_class i ON i.oid = ix.indexrelid
    JOIN pg_class t ON t.oid = ix.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = current_schema()
      AND i.relname = 'uq_credit_tx_job_refund';

    IF rec.table_name IS NOT NULL THEN
        pred := lower(coalesce(rec.predicate_expr, ''));
        -- Perform strict structural validation:
        -- - Table must be credit_transactions in current schema
        -- - Must be UNIQUE
        -- - Exactly one key column: num_key_cols = 1 AND array_length = 1 AND indexed_columns = ['job_id']
        -- - Partial predicate must require job_id is not null and refund transaction type only,
        --   and must not include OR conditions, non-refund transaction types, or unrelated columns
        IF rec.table_name = 'credit_transactions'
           AND rec.is_unique = TRUE
           AND rec.num_key_cols = 1
           AND array_length(rec.indexed_columns, 1) = 1
           AND rec.indexed_columns = ARRAY['job_id']::name[]
           AND rec.predicate_expr IS NOT NULL
           AND pred ~ '\mjob_id\M\s+is\s+not\s+null'
           AND pred ~ '\mtransaction_type\M'
           AND pred ~ '''refund'''
           AND NOT (pred ~ '\mor\M')
           AND NOT (regexp_replace(pred, '''refund''', '', 'gi') ~ '''')
           AND NOT (pred ~ '\m(id|tenant_id|wallet_id|user_id|amount|balance_after|idempotency_key|description|created_at)\M')
        THEN
            is_valid := TRUE;
        END IF;

        IF NOT is_valid THEN
            -- Safely drop only uq_credit_tx_job_refund in current_schema()
            IF EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = current_schema()
                  AND t.relname = 'credit_transactions'
                  AND c.conname = 'uq_credit_tx_job_refund'
            ) THEN
                EXECUTE format('ALTER TABLE %I.credit_transactions DROP CONSTRAINT uq_credit_tx_job_refund', current_schema());
            ELSE
                EXECUTE format('DROP INDEX IF EXISTS %I.uq_credit_tx_job_refund', current_schema());
            END IF;
        END IF;
    END IF;
END $$;

-- 3. Pre-migration check for existing duplicate refund transactions.
-- Does NOT delete, truncate, or silently modify historical ledger records.
-- If duplicates exist, halts the migration and raises a descriptive error.
DO $$
DECLARE
    dup_count INTEGER;
    dup_jobs TEXT;
BEGIN
    SELECT COUNT(*), string_agg(job_id, ', ')
    INTO dup_count, dup_jobs
    FROM (
        SELECT job_id
        FROM credit_transactions
        WHERE transaction_type IN ('refund', 'REFUND') AND job_id IS NOT NULL
        GROUP BY job_id
        HAVING COUNT(*) > 1
    ) sub;

    IF dup_count > 0 THEN
        RAISE EXCEPTION 'Migration 0005 aborted: found % job(s) with duplicate refund transactions in credit_transactions (job IDs: %). Historical ledger entries must not be silently modified; resolve duplicates manually.', dup_count, dup_jobs;
    END IF;
END $$;

-- 4. Create partial unique index enforcing at most one refund per job.
-- Non-refund transactions (allocation, reservation, consumption) are not restricted by this index,
-- preserving append-only ledger semantics while guaranteeing exactly-once refund execution.
CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_tx_job_refund
ON credit_transactions (job_id)
WHERE transaction_type IN ('refund', 'REFUND') AND job_id IS NOT NULL;
