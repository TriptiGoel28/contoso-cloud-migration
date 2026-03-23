import pytest
import psycopg2
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://contoso:contoso_secret@localhost:5432/contoso")


@pytest.fixture(scope="session")
def db_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    yield conn
    conn.close()
