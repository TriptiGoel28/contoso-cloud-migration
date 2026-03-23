"""
Smoke Tests — Contoso Financial Migration
==========================================
Verify basic service availability before and after cutover.

LOCAL:   pytest tests/smoke/ -v
AWS:     WEBAPP_URL=https://your-alb.us-east-1.amazonaws.com pytest tests/smoke/ -v

If any smoke test fails, do not proceed with cutover.
All smoke tests must be green before traffic is shifted.
"""
import os
import time
import requests
import psycopg2
import redis

WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8080")
MINIO_URL = os.getenv("MINIO_URL", "http://localhost:9000")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://contoso:contoso_secret@localhost:5432/contoso")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


class TestWebAppAvailability:

    def test_webapp_health(self):
        """Health endpoint returns 200 with all dependencies OK."""
        resp = requests.get(f"{WEBAPP_URL}/health", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok", f"status not ok: {body}"
        assert body["db"] == "ok", f"db not ok: {body}"
        assert body["redis"] == "ok", f"redis not ok: {body}"

    def test_webapp_health_response_time(self):
        """Health endpoint responds within 2 seconds."""
        start = time.time()
        requests.get(f"{WEBAPP_URL}/health", timeout=5)
        elapsed = time.time() - start
        assert elapsed < 2.0, f"Health check took {elapsed:.2f}s — exceeds 2s SLA"

    def test_webapp_root(self):
        """Dashboard page loads successfully."""
        resp = requests.get(f"{WEBAPP_URL}/", timeout=10)
        assert resp.status_code == 200

    def test_webapp_customers_endpoint(self):
        """Customers API returns 200 with expected structure."""
        resp = requests.get(f"{WEBAPP_URL}/api/customers", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert "customers" in body, f"Missing 'customers' key: {body}"
        assert isinstance(body["customers"], list)

    def test_webapp_404_returns_json(self):
        """Unknown routes return JSON error (not HTML)."""
        resp = requests.get(f"{WEBAPP_URL}/this-does-not-exist", timeout=10)
        assert resp.status_code == 404
        assert "application/json" in resp.headers.get("Content-Type", "")


class TestMinIOAvailability:

    def test_minio_health(self):
        """MinIO (S3) health endpoint responds."""
        resp = requests.get(f"{MINIO_URL}/minio/health/live", timeout=10)
        assert resp.status_code == 200

    def test_minio_readiness(self):
        """MinIO readiness probe passes."""
        resp = requests.get(f"{MINIO_URL}/minio/health/ready", timeout=10)
        assert resp.status_code == 200


class TestRedisAvailability:

    def test_redis_ping(self, redis_client):
        """Redis responds to PING."""
        assert redis_client.ping() is True

    def test_redis_set_get(self, redis_client):
        """Redis read/write works."""
        redis_client.set("smoke:test", "ok", ex=10)
        assert redis_client.get("smoke:test") == "ok"


class TestDatabaseAvailability:

    def test_db_connectivity(self, db_conn):
        """Database accepts connections and responds to queries."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT 1")
            result = cur.fetchone()
        assert result[0] == 1

    def test_db_schemas_exist(self, db_conn):
        """Both app and reporting schemas are present."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name IN ('app', 'reporting')"
            )
            schemas = {row[0] for row in cur.fetchall()}
        assert "app" in schemas, "Schema 'app' not found"
        assert "reporting" in schemas, "Schema 'reporting' not found"

    def test_db_tables_exist(self, db_conn):
        """Core tables are present in the app schema."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'app'"
            )
            tables = {row[0] for row in cur.fetchall()}
        assert "customers" in tables, "Table app.customers not found"
        assert "transactions" in tables, "Table app.transactions not found"

    def test_reconciliation_view_exists(self, db_conn):
        """Cross-schema reporting view is accessible — this is the BI teams' canary."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM reporting.customer_reconciliation_summary")
            count = cur.fetchone()[0]
        assert isinstance(count, int)
        # View returning 0 rows is acceptable; an exception means the view is broken

    def test_db_has_seed_data(self, db_conn):
        """Database contains at least some customer records."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM app.customers")
            count = cur.fetchone()[0]
        assert count > 0, "No customers in database — seed data missing"
