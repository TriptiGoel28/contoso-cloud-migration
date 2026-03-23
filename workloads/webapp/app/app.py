"""
Contoso Financial - Customer-Facing Web Application
Python 3.11 / Flask 3.0

Cloud-native rewrite of the legacy Python 2.7 Flask app. Key changes from the on-prem version:
  - Configuration read from environment variables, NOT from config.ini
  - SQLAlchemy 2.x with connection pooling (replaces per-request connections)
  - Redis client with lazy initialization (app no longer crashes if Redis is down at startup)
  - /internal/report-export now queries the DB directly instead of reading from /mnt/reports/shared
  - Structured logging to stdout (captured by CloudWatch Logs via the awslogs ECS log driver)
  - Health check endpoint returns DB and Redis status for ALB target group health checks
"""

import csv
import io
import logging
import os
import sys
from datetime import datetime, timezone

import redis as redis_lib
from flask import Flask, Response, g, jsonify, request
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------------------
# Logging: structured output to stdout for CloudWatch Logs
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("contoso.webapp")


# ---------------------------------------------------------------------------
# Configuration: all values come from environment variables.
# In AWS, these are injected by ECS from SSM Parameter Store at task startup.
# Locally, they come from docker-compose.yml environment section or .env file.
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")


# ---------------------------------------------------------------------------
# SQLAlchemy 2.x engine and session factory
# pool_size=5 keeps persistent connections alive (important for Fargate tasks
# that receive sustained traffic). max_overflow=10 allows burst headroom.
# pool_pre_ping=True validates connections before use (handles RDS failover).
# ---------------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,  # recycle connections every hour to avoid RDS idle timeout
    echo=False,
)
SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = {"schema": "app"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    account_number = Column(String(50), unique=True, nullable=False)
    email = Column(String(255))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "account_number": self.account_number,
            "email": self.email,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = {"schema": "app"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    type = Column(String(20), nullable=False)  # 'credit' or 'debit'
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reconciled = Column(Boolean, default=False, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "amount": float(self.amount) if self.amount is not None else None,
            "type": self.type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "reconciled": self.reconciled,
        }


# ---------------------------------------------------------------------------
# Redis client
# Initialized lazily -- if Redis is unavailable, only cache-dependent
# operations fail, not the entire application. This fixes a critical bug in
# the on-prem version where a Redis outage at startup crashed the app.
# ---------------------------------------------------------------------------
_redis_client = None


def get_redis():
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        except Exception as exc:
            logger.warning("Redis client initialization failed: %s", exc)
            return None
    return _redis_client


# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY


@app.before_request
def open_db_session():
    """Open a SQLAlchemy session for the duration of the request."""
    g.db = SessionFactory()


@app.teardown_request
def close_db_session(exc):
    """Close the SQLAlchemy session after each request, rolling back on error."""
    db: Session = g.pop("db", None)
    if db is not None:
        if exc is not None:
            db.rollback()
        db.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    """
    Health check endpoint consumed by:
    - ALB target group health check (AWS production)
    - Docker Compose healthcheck (local development)
    Returns 200 only when both DB and Redis are reachable.
    """
    db_status = "ok"
    redis_status = "ok"
    http_status = 200

    # Test database connectivity
    try:
        g.db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("Health check DB failure: %s", exc)
        db_status = "error"
        http_status = 503

    # Test Redis connectivity (non-fatal: Redis down degrades, not kills, the app)
    try:
        r = get_redis()
        if r is None or not r.ping():
            redis_status = "error"
    except Exception as exc:
        logger.warning("Health check Redis failure: %s", exc)
        redis_status = "error"

    return jsonify({"status": "ok" if http_status == 200 else "degraded", "db": db_status, "redis": redis_status}), http_status


@app.route("/")
def dashboard():
    """
    Dashboard page. Returns a JSON summary (in a real UI this would render HTML).
    Queries customer count and the 10 most recent transactions.
    Results are cached in Redis for 60 seconds to reduce DB load.
    """
    cache_key = "dashboard:summary"
    r = get_redis()

    # Attempt to serve from cache
    if r is not None:
        try:
            cached = r.get(cache_key)
            if cached:
                import json
                return jsonify(json.loads(cached))
        except Exception as exc:
            logger.warning("Redis cache read failed: %s", exc)

    try:
        customer_count = g.db.execute(text("SELECT COUNT(*) FROM app.customers")).scalar()
        rows = g.db.execute(
            text(
                "SELECT id, customer_id, amount, type, created_at, reconciled "
                "FROM app.transactions ORDER BY created_at DESC LIMIT 10"
            )
        ).fetchall()
    except Exception as exc:
        logger.error("Dashboard query failed: %s", exc)
        return jsonify({"error": "Database query failed"}), 500

    recent_transactions = [
        {
            "id": row.id,
            "customer_id": row.customer_id,
            "amount": float(row.amount),
            "type": row.type,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "reconciled": row.reconciled,
        }
        for row in rows
    ]

    payload = {
        "customer_count": customer_count,
        "recent_transactions": recent_transactions,
    }

    # Cache the result for 60 seconds
    if r is not None:
        try:
            import json
            r.setex(cache_key, 60, json.dumps(payload))
        except Exception as exc:
            logger.warning("Redis cache write failed: %s", exc)

    return jsonify(payload)


@app.route("/api/customers")
def list_customers():
    """
    Paginated customer list.
    Query params: page (default 1), per_page (default 20, max 100)
    """
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid pagination parameters"}), 400

    offset = (page - 1) * per_page

    try:
        total = g.db.execute(text("SELECT COUNT(*) FROM app.customers")).scalar()
        rows = g.db.execute(
            text("SELECT id, name, account_number, email, created_at FROM app.customers ORDER BY id LIMIT :limit OFFSET :offset"),
            {"limit": per_page, "offset": offset},
        ).fetchall()
    except Exception as exc:
        logger.error("Customer list query failed: %s", exc)
        return jsonify({"error": "Database query failed"}), 500

    customers = [
        {
            "id": row.id,
            "name": row.name,
            "account_number": row.account_number,
            "email": row.email,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]

    return jsonify({
        "customers": customers,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if total else 0,
    })


@app.route("/api/transactions", methods=["POST"])
def create_transaction():
    """
    Create a new transaction. Invalidates the dashboard Redis cache on success.
    Expected JSON body: {"customer_id": int, "amount": float, "type": "credit"|"debit"}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # Validate required fields
    customer_id = data.get("customer_id")
    amount = data.get("amount")
    transaction_type = data.get("type")

    if customer_id is None or amount is None or transaction_type is None:
        return jsonify({"error": "Missing required fields: customer_id, amount, type"}), 400

    # Validate amount is positive
    try:
        amount = float(amount)
        if amount <= 0:
            return jsonify({"error": "amount must be greater than 0"}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a valid number"}), 400

    # Validate transaction type
    if transaction_type not in ("credit", "debit"):
        return jsonify({"error": "type must be 'credit' or 'debit'"}), 400

    # Verify the customer exists
    try:
        customer_exists = g.db.execute(
            text("SELECT 1 FROM app.customers WHERE id = :id"),
            {"id": customer_id},
        ).fetchone()
    except Exception as exc:
        logger.error("Customer lookup failed: %s", exc)
        return jsonify({"error": "Database error"}), 500

    if not customer_exists:
        return jsonify({"error": f"Customer {customer_id} not found"}), 404

    # Insert the transaction
    try:
        result = g.db.execute(
            text(
                "INSERT INTO app.transactions (customer_id, amount, type, reconciled) "
                "VALUES (:customer_id, :amount, :type, false) "
                "RETURNING id, customer_id, amount, type, created_at, reconciled"
            ),
            {"customer_id": customer_id, "amount": amount, "type": transaction_type},
        ).fetchone()
        g.db.commit()
    except Exception as exc:
        g.db.rollback()
        logger.error("Transaction insert failed: %s", exc)
        return jsonify({"error": "Failed to create transaction"}), 500

    # Invalidate the dashboard cache so the next request reflects the new transaction
    r = get_redis()
    if r is not None:
        try:
            r.delete("dashboard:summary")
        except Exception as exc:
            logger.warning("Redis cache invalidation failed: %s", exc)

    logger.info("Created transaction id=%s customer_id=%s amount=%s type=%s", result.id, customer_id, amount, transaction_type)

    return jsonify({
        "id": result.id,
        "customer_id": result.customer_id,
        "amount": float(result.amount),
        "type": result.type,
        "created_at": result.created_at.isoformat() if result.created_at else None,
        "reconciled": result.reconciled,
    }), 201


@app.route("/internal/report-export")
def report_export():
    """
    Export reconciled transaction data as CSV.

    CLOUD-NATIVE CHANGE: The on-prem version of this endpoint read files
    directly from /mnt/reports/shared/exports/ via NFS mount. That approach
    caused 30-second hangs when the NFS mount was unavailable and exposed
    raw filesystem contents with no access control.

    This version queries the reporting.reconciled_transactions table directly
    and streams the result as CSV. No filesystem dependency. No NFS mount.
    The data is always current (no stale file lag).

    In a production deployment, this endpoint should be protected by
    authentication (e.g., an internal ALB listener rule or an API key header).
    """
    try:
        rows = g.db.execute(
            text(
                "SELECT rt.id, rt.transaction_id, c.name AS customer_name, "
                "c.account_number, rt.amount, rt.reconciled_at, rt.batch_run_id, rt.status "
                "FROM reporting.reconciled_transactions rt "
                "LEFT JOIN app.customers c ON c.id = rt.customer_id "
                "ORDER BY rt.reconciled_at DESC NULLS LAST "
                "LIMIT 10000"
            )
        ).fetchall()
    except Exception as exc:
        logger.error("Report export query failed: %s", exc)
        return jsonify({"error": "Failed to generate report"}), 500

    # Build CSV in memory and stream it as a response
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "transaction_id", "customer_name", "account_number", "amount", "reconciled_at", "batch_run_id", "status"])
    for row in rows:
        writer.writerow([
            row.id,
            row.transaction_id,
            row.customer_name,
            row.account_number,
            row.amount,
            row.reconciled_at.isoformat() if row.reconciled_at else "",
            row.batch_run_id,
            row.status,
        ])

    csv_content = output.getvalue()
    filename = f"reconciliation_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"

    return Response(
        csv_content,
        status=200,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # For local development only. In production, gunicorn is used as the WSGI server.
    app.run(host="0.0.0.0", port=8080, debug=False)
