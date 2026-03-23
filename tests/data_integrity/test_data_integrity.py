"""
Data Integrity Tests — Contoso Financial Migration
===================================================
These tests define what "migration succeeded" means.

PRE-CUTOVER:  Run against local Docker stack.
              ALL tests must pass before cutover begins.
              A failure here means the on-prem data has issues to fix first.

POST-CUTOVER: Run against AWS RDS:
              DATABASE_URL=postgresql://contoso:<pwd>@<rds-endpoint>:5432/contoso \\
              pytest tests/data_integrity/ -v

A post-cutover failure triggers the rollback plan (docs/10-rollback.md).

Author's note: test_cross_schema_view_accessible is the canary for the most
complex migration dependency. If this fails post-cutover, the BI teams'
Metabase queries are broken and 4 teams are affected immediately.
"""
import os
import pytest
import psycopg2
import psycopg2.errors

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://contoso:contoso_secret@localhost:5432/contoso")


class TestSchemaIntegrity:

    def test_all_schemas_present(self, db_conn):
        """Both app and reporting schemas exist."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name IN ('app', 'reporting')"
            )
            schemas = {row[0] for row in cur.fetchall()}
        assert schemas == {"app", "reporting"}, f"Missing schemas. Found: {schemas}"

    def test_all_tables_present(self, db_conn):
        """All expected tables exist in both schemas."""
        expected = {
            ("app", "customers"),
            ("app", "transactions"),
            ("reporting", "reconciled_transactions"),
            ("reporting", "batch_runs"),
        }
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema IN ('app', 'reporting')"
            )
            found = {(row[0], row[1]) for row in cur.fetchall()}
        missing = expected - found
        assert not missing, f"Missing tables: {missing}"

    def test_cross_schema_view_accessible(self, db_conn):
        """Cross-schema reporting view is accessible — canary for BI team connectivity."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM reporting.customer_reconciliation_summary")
            count = cur.fetchone()[0]
        assert isinstance(count, int), "View returned non-integer count"

    def test_cross_schema_view_has_expected_columns(self, db_conn):
        """reporting.customer_reconciliation_summary has all expected columns."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'reporting' "
                "AND table_name = 'customer_reconciliation_summary'"
            )
            columns = {row[0] for row in cur.fetchall()}
        expected_columns = {
            "customer_id", "customer_name", "account_number",
            "total_reconciled", "total_credits", "total_debits", "last_reconciled_at"
        }
        missing = expected_columns - columns
        assert not missing, f"View missing columns: {missing}"


class TestDataQuality:

    def test_customer_count_nonzero(self, db_conn):
        """Database contains at least one customer record."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM app.customers")
            count = cur.fetchone()[0]
        assert count > 0, "app.customers is empty — seed data missing or migration incomplete"

    def test_no_orphaned_transactions(self, db_conn):
        """All transactions reference a valid customer (referential integrity)."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM app.transactions t "
                "LEFT JOIN app.customers c ON t.customer_id = c.id "
                "WHERE c.id IS NULL"
            )
            orphaned = cur.fetchone()[0]
        assert orphaned == 0, f"Found {orphaned} orphaned transactions with no matching customer"

    def test_no_negative_amounts(self, db_conn):
        """All transaction amounts are positive (enforced by DB constraint)."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM app.transactions WHERE amount <= 0")
            bad = cur.fetchone()[0]
        assert bad == 0, f"Found {bad} transactions with amount <= 0"

    def test_transaction_types_valid(self, db_conn):
        """All transaction types are either 'credit' or 'debit'."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT DISTINCT type FROM app.transactions")
            types = {row[0] for row in cur.fetchall()}
        invalid = types - {"credit", "debit"}
        assert not invalid, f"Invalid transaction types found: {invalid}"

    def test_account_numbers_unique(self, db_conn):
        """Customer account numbers are unique (no duplicates introduced during migration)."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT account_number, COUNT(*) FROM app.customers "
                "GROUP BY account_number HAVING COUNT(*) > 1"
            )
            duplicates = cur.fetchall()
        assert not duplicates, f"Duplicate account numbers: {duplicates}"


class TestReconciliationIntegrity:

    def test_reconciled_transactions_have_batch_run_id(self, db_conn):
        """Every reconciliation record has a non-null batch_run_id (idempotency key)."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM reporting.reconciled_transactions "
                "WHERE batch_run_id IS NULL OR batch_run_id = ''"
            )
            bad = cur.fetchone()[0]
        assert bad == 0, f"Found {bad} reconciliation records without batch_run_id"

    def test_no_duplicate_reconciliation(self, db_conn):
        """No transaction has been reconciled more than once (deduplication check)."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT transaction_id, COUNT(*) AS cnt "
                "FROM reporting.reconciled_transactions "
                "GROUP BY transaction_id HAVING COUNT(*) > 1"
            )
            duplicates = cur.fetchall()
        assert not duplicates, (
            f"Duplicate reconciliation records found for transaction IDs: "
            f"{[row[0] for row in duplicates]}. "
            f"Run the deduplication query from docs/10-rollback.md."
        )

    def test_app_reconciled_flag_consistent(self, db_conn):
        """Every transaction marked reconciled=true has a matching reconciliation record."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM app.transactions t "
                "WHERE t.reconciled = true "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM reporting.reconciled_transactions rt "
                "  WHERE rt.transaction_id = t.id"
                ")"
            )
            inconsistent = cur.fetchone()[0]
        assert inconsistent == 0, (
            f"Found {inconsistent} transactions marked reconciled=true "
            f"with no matching entry in reporting.reconciled_transactions"
        )

    def test_batch_runs_table_accessible(self, db_conn):
        """batch_runs audit table is accessible."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM reporting.batch_runs")
            count = cur.fetchone()[0]
        assert isinstance(count, int)


class TestAccessControl:

    def test_reporting_readonly_cannot_insert(self):
        """reporting_readonly role cannot write to the reporting schema."""
        # Build a connection string with the reporting_readonly role
        # Replace the username in DATABASE_URL
        base_url = os.getenv("DATABASE_URL", "postgresql://contoso:contoso_secret@localhost:5432/contoso")
        readonly_url = base_url.replace(
            "postgresql://contoso:contoso_secret@",
            "postgresql://reporting_readonly:readonly_password@"
        )

        try:
            conn = psycopg2.connect(readonly_url)
        except psycopg2.OperationalError:
            pytest.xfail(
                "reporting_readonly role not configured with password in this environment. "
                "In AWS, this role uses IAM authentication. Skipping write restriction test."
            )
            return

        try:
            with conn.cursor() as cur:
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute(
                        "INSERT INTO reporting.reconciled_transactions "
                        "(transaction_id, customer_id, amount, batch_run_id) "
                        "VALUES (99999, 1, 100.00, 'test-readonly-check')"
                    )
        finally:
            conn.close()
