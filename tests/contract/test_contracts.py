"""
Contract Tests — Contoso Financial Migration
=============================================
Verify API request/response schemas.

Run against local stack before cutover.
Run against AWS post-cutover with WEBAPP_URL env var.

A contract test failure post-cutover = API regression = rollback.

LOCAL:  pytest tests/contract/ -v
AWS:    WEBAPP_URL=https://your-alb.us-east-1.amazonaws.com pytest tests/contract/ -v
"""
import os
import pytest
import requests

WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8080")
SHIM_URL = os.getenv("SHIM_URL", "http://localhost:8081")
SHIM_AVAILABLE = os.getenv("SHIM_URL") is not None


class TestHealthContract:

    def test_health_schema(self):
        """Health endpoint returns exact expected schema."""
        resp = requests.get(f"{WEBAPP_URL}/health", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        expected_keys = {"status", "db", "redis", "version"}
        assert set(body.keys()) == expected_keys, (
            f"Health response keys mismatch. Got: {set(body.keys())}, expected: {expected_keys}"
        )
        assert isinstance(body["version"], str)


class TestCustomersContract:

    def test_customers_list_schema(self):
        """GET /api/customers returns paginated response with correct schema."""
        resp = requests.get(f"{WEBAPP_URL}/api/customers", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert "customers" in body
        assert "total" in body
        assert "page" in body
        assert "pages" in body
        assert isinstance(body["customers"], list)
        assert isinstance(body["total"], int)

    def test_customers_pagination(self):
        """Pagination params are respected."""
        resp = requests.get(f"{WEBAPP_URL}/api/customers?page=1&per_page=5", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 1
        assert len(body["customers"]) <= 5

    def test_customer_item_schema(self):
        """Each customer object has the required fields."""
        resp = requests.get(f"{WEBAPP_URL}/api/customers?per_page=1", timeout=10)
        assert resp.status_code == 200
        customers = resp.json()["customers"]
        if not customers:
            pytest.skip("No customers in database")
        customer = customers[0]
        assert "id" in customer and isinstance(customer["id"], int)
        assert "name" in customer and isinstance(customer["name"], str)
        assert "account_number" in customer and isinstance(customer["account_number"], str)
        assert "created_at" in customer and isinstance(customer["created_at"], str)


class TestTransactionsContract:

    def test_post_transaction_valid_credit(self, seed_customer_id):
        """Valid credit transaction returns 201 with transaction ID."""
        payload = {"customer_id": seed_customer_id, "amount": 100.00, "type": "credit"}
        resp = requests.post(f"{WEBAPP_URL}/api/transactions", json=payload, timeout=10)
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "id" in body

    def test_post_transaction_valid_debit(self, seed_customer_id):
        """Valid debit transaction returns 201."""
        payload = {"customer_id": seed_customer_id, "amount": 50.00, "type": "debit"}
        resp = requests.post(f"{WEBAPP_URL}/api/transactions", json=payload, timeout=10)
        assert resp.status_code == 201

    def test_post_transaction_negative_amount(self, seed_customer_id):
        """Negative amount is rejected with 400."""
        payload = {"customer_id": seed_customer_id, "amount": -10.00, "type": "credit"}
        resp = requests.post(f"{WEBAPP_URL}/api/transactions", json=payload, timeout=10)
        assert resp.status_code == 400, f"Expected 400 for negative amount, got {resp.status_code}"

    def test_post_transaction_zero_amount(self, seed_customer_id):
        """Zero amount is rejected with 400."""
        payload = {"customer_id": seed_customer_id, "amount": 0, "type": "credit"}
        resp = requests.post(f"{WEBAPP_URL}/api/transactions", json=payload, timeout=10)
        assert resp.status_code == 400

    def test_post_transaction_invalid_type(self, seed_customer_id):
        """Invalid transaction type is rejected with 400."""
        payload = {"customer_id": seed_customer_id, "amount": 10.00, "type": "wire"}
        resp = requests.post(f"{WEBAPP_URL}/api/transactions", json=payload, timeout=10)
        assert resp.status_code == 400

    def test_post_transaction_nonexistent_customer(self):
        """Transaction for non-existent customer is rejected."""
        payload = {"customer_id": 999999, "amount": 10.00, "type": "credit"}
        resp = requests.post(f"{WEBAPP_URL}/api/transactions", json=payload, timeout=10)
        assert resp.status_code in (400, 404)


class TestReportExportContract:

    def test_report_export_returns_csv(self):
        """Report export endpoint returns CSV content."""
        resp = requests.get(f"{WEBAPP_URL}/internal/report-export", timeout=15)
        assert resp.status_code == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "text/csv" in content_type, (
            f"Expected text/csv, got: {content_type}"
        )


class TestReconciliationShimContract:

    @pytest.mark.skipif(not SHIM_AVAILABLE, reason="SHIM_URL not set — shim not deployed")
    def test_shim_health(self):
        """Compatibility shim health endpoint responds."""
        resp = requests.get(f"{SHIM_URL}/health", timeout=10)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.skipif(not SHIM_AVAILABLE, reason="SHIM_URL not set — shim not deployed")
    def test_shim_status_endpoint(self):
        """Reconciliation status endpoint returns 200 or 404 (no runs yet)."""
        resp = requests.get(f"{SHIM_URL}/reconciliation/status", timeout=10)
        # 404 is acceptable if no batch runs have been completed yet
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert "batch_run_id" in body
            assert "reconciled_count" in body
            assert "status" in body
