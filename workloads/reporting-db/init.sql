-- =============================================================================
-- Contoso Financial - Reporting Database Initialization
-- PostgreSQL 15 (Amazon RDS)
--
-- MIGRATION RISK: Cross-Schema Dependency
-- This schema intentionally contains a VIEW in the reporting schema that JOINs
-- app.customers and reporting.reconciled_transactions. This cross-schema JOIN
-- was identified in the Discovery document (docs/02-discovery.md) as a
-- migration risk because it prevents separating the app and reporting schemas
-- onto different database instances without rewriting the view and updating
-- the five BI teams Metabase connections.
--
-- Resolution: both schemas are migrated together to the same RDS instance.
-- The views are preserved unchanged. Separating the schemas is deferred to
-- a future database refactoring initiative after migration is complete.
-- =============================================================================

-- Schemas
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS reporting;

-- ---------------------------------------------------------------------------
-- app.customers
-- Master customer record. Owned by the web application.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.customers (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(255)    NOT NULL,
    account_number VARCHAR(50)     NOT NULL UNIQUE,
    email          VARCHAR(255),
    created_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE app.customers IS 'Master customer records. Written by the web application only.';

-- ---------------------------------------------------------------------------
-- app.transactions
-- Financial transactions recorded by the web application.
-- The batch reconciliation job updates the reconciled column via a dedicated
-- database role (batch_reconciler) with UPDATE-only access on that column.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.transactions (
    id          SERIAL PRIMARY KEY,
    customer_id INT             NOT NULL REFERENCES app.customers(id) ON DELETE RESTRICT,
    amount      NUMERIC(15, 2)  NOT NULL,
    type        VARCHAR(20)     NOT NULL CHECK (type IN ('credit', 'debit')),
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    reconciled  BOOLEAN         NOT NULL DEFAULT FALSE
);

COMMENT ON TABLE app.transactions IS 'Financial transactions. reconciled=true means the batch job has processed this row.';
COMMENT ON COLUMN app.transactions.reconciled IS 'Set to true by the batch reconciliation job. Never reset to false once true.';

CREATE INDEX IF NOT EXISTS idx_transactions_customer_id  ON app.transactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_transactions_reconciled   ON app.transactions(reconciled) WHERE reconciled = FALSE;
CREATE INDEX IF NOT EXISTS idx_transactions_created_at   ON app.transactions(created_at DESC);

-- ---------------------------------------------------------------------------
-- reporting.reconciled_transactions
-- Written exclusively by the batch reconciliation job.
-- The web application reads this table (via the summary view) but never writes to it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reporting.reconciled_transactions (
    id             SERIAL PRIMARY KEY,
    transaction_id INT             NOT NULL,
    customer_id    INT             NOT NULL,
    amount         NUMERIC(15, 2)  NOT NULL,
    reconciled_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    batch_run_id   VARCHAR(50)     NOT NULL,
    status         VARCHAR(20)     NOT NULL DEFAULT 'reconciled'
        CHECK (status IN ('reconciled', 'failed', 'duplicate'))
);

COMMENT ON TABLE reporting.reconciled_transactions IS
    'Written by the batch reconciliation job. Each row represents one reconciled transaction. '
    'batch_run_id groups all rows from a single batch run for idempotency checks.';
COMMENT ON COLUMN reporting.reconciled_transactions.batch_run_id IS
    'UUID assigned to each batch run. Used to detect and roll back duplicate runs.';
COMMENT ON COLUMN reporting.reconciled_transactions.status IS
    'reconciled = processed successfully, failed = validation error, duplicate = already reconciled.';

CREATE INDEX IF NOT EXISTS idx_recon_transaction_id  ON reporting.reconciled_transactions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_recon_customer_id     ON reporting.reconciled_transactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_recon_batch_run_id    ON reporting.reconciled_transactions(batch_run_id);

-- Unique constraint to prevent double-reconciliation of the same transaction.
-- On conflict, the batch job marks the duplicate row with status='duplicate'.
CREATE UNIQUE INDEX IF NOT EXISTS idx_recon_transaction_unique
    ON reporting.reconciled_transactions(transaction_id)
    WHERE status = 'reconciled';

-- ---------------------------------------------------------------------------
-- reporting.customer_reconciliation_summary
-- Cross-schema view: JOINs app.customers and reporting.reconciled_transactions.
--
-- THIS VIEW IS THE CROSS-SCHEMA DEPENDENCY identified in the Discovery doc.
-- All five BI teams query this via Metabase. Do not rename columns without
-- coordinating with: Finance Ops, Audit and Compliance, Risk Management,
-- Executive Reporting, and Data Engineering teams.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW reporting.customer_reconciliation_summary AS
SELECT
    c.id                                                    AS customer_id,
    c.name                                                  AS customer_name,
    c.account_number,
    c.email,
    COUNT(rt.id)                                            AS reconciled_count,
    COALESCE(SUM(rt.amount), 0)                             AS total_reconciled_amount,
    MAX(rt.reconciled_at)                                   AS last_reconciled_at,
    COUNT(CASE WHEN rt.status = 'failed'    THEN 1 END)     AS failed_count,
    COUNT(CASE WHEN rt.status = 'duplicate' THEN 1 END)     AS duplicate_count
FROM app.customers c
LEFT JOIN reporting.reconciled_transactions rt ON c.id = rt.customer_id
GROUP BY c.id, c.name, c.account_number, c.email;

COMMENT ON VIEW reporting.customer_reconciliation_summary IS
    'Cross-schema view joining app.customers and reporting.reconciled_transactions. '
    'Queried by all BI teams via Metabase. Migration risk: requires both schemas '
    'to reside on the same database instance.';

-- ---------------------------------------------------------------------------
-- Access Control Roles
-- ---------------------------------------------------------------------------

-- reporting_readonly: SELECT on reporting schema only.
-- Assigned to: Finance Ops, Audit and Compliance, Risk Management teams.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reporting_readonly') THEN
        CREATE ROLE reporting_readonly NOLOGIN NOINHERIT;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA reporting TO reporting_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO reporting_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA reporting TO reporting_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA reporting GRANT SELECT ON TABLES TO reporting_readonly;

-- bi_user: reporting_readonly + SELECT on app.customers (needed for cross-schema view).
-- Assigned to: Executive Reporting team Metabase instances.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_user') THEN
        CREATE ROLE bi_user NOLOGIN NOINHERIT;
    END IF;
END
$$;

GRANT reporting_readonly TO bi_user;
GRANT USAGE ON SCHEMA app TO bi_user;
GRANT SELECT ON app.customers TO bi_user;

-- batch_reconciler: replaces the dangerous postgres-superuser cron pattern.
-- Has only the minimum permissions needed for the reconciliation job:
--   SELECT on app.transactions (to verify transaction exists and is not yet reconciled)
--   UPDATE reconciled on app.transactions (to mark as done)
--   SELECT + INSERT on reporting.reconciled_transactions (to write results)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'batch_reconciler') THEN
        CREATE ROLE batch_reconciler NOLOGIN NOINHERIT;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA app TO batch_reconciler;
GRANT USAGE ON SCHEMA reporting TO batch_reconciler;
GRANT SELECT ON app.transactions TO batch_reconciler;
GRANT UPDATE (reconciled) ON app.transactions TO batch_reconciler;
GRANT SELECT, INSERT ON reporting.reconciled_transactions TO batch_reconciler;
GRANT USAGE ON SEQUENCE reporting.reconciled_transactions_id_seq TO batch_reconciler;
GRANT SELECT ON app.customers TO batch_reconciler;

-- ---------------------------------------------------------------------------
-- Seed Data: 10 sample customers
-- ---------------------------------------------------------------------------
INSERT INTO app.customers (name, account_number, email) VALUES
    ('Northwind Trading Co.',        'CNT-000001', 'accounts@northwindtrading.com'),
    ('Blue Sky Financial Partners',  'CNT-000002', 'finance@blueskyfp.com'),
    ('Riverside Manufacturing Ltd.', 'CNT-000003', 'ap@riverside-mfg.com'),
    ('Harbor Point Logistics',       'CNT-000004', 'billing@harborpointlogistics.com'),
    ('Summit Capital Group',         'CNT-000005', 'treasury@summitcapital.com'),
    ('Clearwater Solutions Inc.',    'CNT-000006', 'invoices@clearwatersolutions.com'),
    ('Meridian Asset Management',    'CNT-000007', 'ops@meridianassets.com'),
    ('Crestwood Technologies LLC',   'CNT-000008', 'finance@crestwoodtech.com'),
    ('Lakeside Property Group',      'CNT-000009', 'accounts@lakesidepropertygroup.com'),
    ('Ironbridge Consulting LLC',    'CNT-000010', 'billing@ironbridgeconsulting.com')
ON CONFLICT (account_number) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Seed Data: 20 sample transactions
-- ---------------------------------------------------------------------------
INSERT INTO app.transactions (customer_id, amount, type, reconciled) VALUES
    (1,   12500.00, 'credit',  true),
    (1,    4300.50, 'debit',   true),
    (2,   87000.00, 'credit',  true),
    (2,   12000.00, 'debit',   false),
    (3,    9850.75, 'credit',  true),
    (3,    2200.00, 'debit',   true),
    (4,   55000.00, 'credit',  false),
    (4,   18500.00, 'debit',   false),
    (5,  125000.00, 'credit',  true),
    (5,   43000.00, 'debit',   true),
    (6,    6700.25, 'credit',  true),
    (6,    1500.00, 'debit',   false),
    (7,  230000.00, 'credit',  true),
    (7,   89000.00, 'debit',   true),
    (8,   15300.00, 'credit',  false),
    (8,    7800.50, 'debit',   false),
    (9,   42100.00, 'credit',  true),
    (9,   18200.00, 'debit',   true),
    (10,  77500.00, 'credit',  true),
    (10,  31000.00, 'debit',   false);

-- ---------------------------------------------------------------------------
-- Seed Data: reconciled_transactions for all reconciled=true rows
-- Fixed batch_run_id makes this seed idempotent on re-run
-- ---------------------------------------------------------------------------
INSERT INTO reporting.reconciled_transactions
    (transaction_id, customer_id, amount, reconciled_at, batch_run_id, status)
SELECT
    t.id,
    t.customer_id,
    t.amount,
    NOW() - INTERVAL '1 day',
    '00000000-seed-0000-0000-000000000001',
    'reconciled'
FROM app.transactions t
WHERE t.reconciled = true
ON CONFLICT DO NOTHING;
