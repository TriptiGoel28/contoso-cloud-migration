import pytest
import os
import requests

WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8080")
SHIM_URL = os.getenv("SHIM_URL", "http://localhost:8081")


@pytest.fixture(scope="session")
def webapp_url():
    return WEBAPP_URL


@pytest.fixture(scope="session")
def shim_url():
    return SHIM_URL


@pytest.fixture(scope="session")
def seed_customer_id():
    """Returns a valid customer ID for use in transaction tests."""
    try:
        resp = requests.get(f"{WEBAPP_URL}/api/customers?per_page=1", timeout=5)
        if resp.status_code == 200:
            customers = resp.json().get("customers", [])
            if customers:
                return customers[0]["id"]
    except Exception:
        pass
    return 1
