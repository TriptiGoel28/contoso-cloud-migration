import pytest
import psycopg2
import redis
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://contoso:contoso_secret@localhost:5432/contoso")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture(scope="session")
def db_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def redis_client():
    client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)
    yield client
