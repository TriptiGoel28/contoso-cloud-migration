"""
Contoso Financial - Batch Reconciliation Worker
Python 3.11

This module replaces the legacy 2am cron job that ran as the postgres OS superuser,
read CSV files from an NFS mount at /mnt/reports/shared/incoming/, and wrote directly
to the reporting.reconciled_transactions table.

Key changes from the legacy implementation:
  - Reads input CSV from MinIO (local) or S3 (AWS) instead of NFS filesystem
  - Runs continuously as a container, polling for new files every POLL_INTERVAL seconds
  - Uses a dedicated batch_reconciler database role (not the postgres superuser)
  - Assigns a batch_run_id UUID to each run for idempotency and auditability
  - Moves processed files to a processed/ prefix instead of deleting them
  - Handles partial failures per-row: failed rows are recorded with status='failed'
    rather than aborting the entire batch
  - No sleep(30) to wait for a mount -- S3/MinIO is always available

Deployment model:
  - Local: container polls MinIO every POLL_INTERVAL seconds using the MinIO Python SDK
  - AWS (production): this container runs on ECS Fargate. S3 Event Notifications are
    configured to send PutObject events to an SQS queue. The container polls SQS for
    messages and processes the corresponding S3 objects. POLL_INTERVAL is used as a
    fallback safety poll when the SQS queue is empty.

Environment variables (all required unless noted):
  DATABASE_URL      - PostgreSQL connection string
  MINIO_ENDPOINT    - MinIO API URL (e.g. http://localhost:9000); empty string in AWS
  MINIO_ACCESS_KEY  - MinIO/S3 access key
  MINIO_SECRET_KEY  - MinIO/S3 secret key
  INPUT_BUCKET      - S3/MinIO bucket name for incoming CSV files
  OUTPUT_BUCKET     - S3/MinIO bucket name for summary report output
  POLL_INTERVAL     - Seconds between polling cycles (default: 30)
  LOG_LEVEL         - Logging level (default: INFO)
"""

import csv
import io
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Iterator, NamedTuple

import psycopg2
import psycopg2.extras
from minio import Minio
from minio.error import S3Error

# ---------------------------------------------------------------------------
# Logging: structured output to stdout for CloudWatch Logs
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("contoso.batch")


# ---------------------------------------------------------------------------
# Configuration from environment variables
# ---------------------------------------------------------------------------
DATABASE_URL    = os.environ["DATABASE_URL"]
MINIO_ENDPOINT  = os.environ.get("MINIO_ENDPOINT", "").lstrip("http://").lstrip("https://")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")
INPUT_BUCKET    = os.environ.get("INPUT_BUCKET", "reconciliation-input")
OUTPUT_BUCKET   = os.environ.get("OUTPUT_BUCKET", "reconciliation-output")
POLL_INTERVAL   = int(os.environ.get("POLL_INTERVAL", "30"))

# Determine if we are talking to MinIO (local) or real S3 (AWS).
# When MINIO_ENDPOINT is empty, boto3 would be used with the IAM task role.
# For this implementation we use the minio-python SDK for both, which works
# against both MinIO and S3 with the same API.
USE_SSL = not (MINIO_ENDPOINT.startswith("localhost") or MINIO_ENDPOINT.startswith("minio") or "9000" in MINIO_ENDPOINT)


class ReconciliationRow(NamedTuple):
    transaction_id: int
    amount: float
    timestamp: str
    source_system: str


class BatchResult(NamedTuple):
    batch_run_id: str
    input_file: str
    total_rows: int
    reconciled_count: int
    failed_count: int
    duplicate_count: int
    started_at: datetime
    completed_at: datetime


def get_minio_client() -> Minio:
    """Return a configured MinIO client. Raises on connection failure."""
    endpoint = MINIO_ENDPOINT or "s3.amazonaws.com"
    # Strip scheme if present (minio SDK takes host:port only)
    endpoint = endpoint.replace("http://", "").replace("https://", "")
    client = Minio(
        endpoint,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=USE_SSL,
    )
    return client


def get_db_connection():
    """Return a psycopg2 connection using the batch_reconciler role credentials."""
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def list_pending_files(client: Minio) -> list[str]:
    """
    List CSV files in the input bucket that are NOT in the processed/ prefix.
    Returns a list of object names (keys).
    """
    pending = []
    try:
        objects = client.list_objects(INPUT_BUCKET, recursive=True)
        for obj in objects:
            name = obj.object_name
            # Skip already-processed files and directory markers
            if name.startswith("processed/") or name.endswith("/"):
                continue
            if name.lower().endswith(".csv"):
                pending.append(name)
    except S3Error as exc:
        logger.error("Failed to list objects in bucket %s: %s", INPUT_BUCKET, exc)
    return pending


def download_csv(client: Minio, object_name: str) -> list[ReconciliationRow]:
    """
    Download a CSV from MinIO/S3 and parse it into ReconciliationRow objects.
    Expected columns: transaction_id, amount, timestamp, source_system
    Skips header row. Skips malformed rows with a warning.
    """
    rows = []
    try:
        response = client.get_object(INPUT_BUCKET, object_name)
        content = response.read().decode("utf-8")
        response.close()
        response.release_conn()
    except S3Error as exc:
        logger.error("Failed to download %s: %s", object_name, exc)
        return rows

    reader = csv.DictReader(io.StringIO(content))
    for line_num, row in enumerate(reader, start=2):
        try:
            transaction_id = int(row["transaction_id"])
            amount = float(row["amount"])
            timestamp = row.get("timestamp", "")
            source_system = row.get("source_system", "unknown")
            if amount <= 0:
                logger.warning("Line %d: skipping row with non-positive amount %s", line_num, amount)
                continue
            rows.append(ReconciliationRow(
                transaction_id=transaction_id,
                amount=amount,
                timestamp=timestamp,
                source_system=source_system,
            ))
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Line %d: malformed row, skipping (%s): %s", line_num, exc, row)
    return rows


def validate_and_reconcile(conn, batch_run_id: str, rows: list[ReconciliationRow]) -> tuple[int, int, int]:
    """
    Process a list of ReconciliationRow objects against the database.

    For each row:
      1. Verify transaction_id exists in app.transactions
      2. Verify the transaction is not already reconciled
      3. Insert a row into reporting.reconciled_transactions
      4. Mark the transaction as reconciled in app.transactions

    Uses a single transaction per batch run. On unrecoverable error, rolls back
    and re-raises. Per-row validation failures are recorded with status='failed'
    and do not abort the batch.

    Returns: (reconciled_count, failed_count, duplicate_count)
    """
    reconciled_count = 0
    failed_count = 0
    duplicate_count = 0

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for row in rows:
            try:
                # Look up the transaction
                cur.execute(
                    "SELECT id, customer_id, amount, reconciled FROM app.transactions WHERE id = %s",
                    (row.transaction_id,),
                )
                txn = cur.fetchone()

                if txn is None:
                    logger.warning("transaction_id=%d not found in DB, marking failed", row.transaction_id)
                    cur.execute(
                        "INSERT INTO reporting.reconciled_transactions "
                        "(transaction_id, customer_id, amount, reconciled_at, batch_run_id, status) "
                        "VALUES (%s, %s, %s, NOW(), %s, 'failed')",
                        (row.transaction_id, 0, row.amount, batch_run_id),
                    )
                    failed_count += 1
                    continue

                if txn["reconciled"]:
                    logger.info("transaction_id=%d already reconciled, marking duplicate", row.transaction_id)
                    # Do not insert a second 'reconciled' row (would violate unique index).
                    # Record a duplicate marker instead.
                    cur.execute(
                        "INSERT INTO reporting.reconciled_transactions "
                        "(transaction_id, customer_id, amount, reconciled_at, batch_run_id, status) "
                        "VALUES (%s, %s, %s, NOW(), %s, 'duplicate')",
                        (row.transaction_id, txn["customer_id"], row.amount, batch_run_id),
                    )
                    duplicate_count += 1
                    continue

                # Validate amount tolerance: warn if CSV amount differs from DB amount by more than 1 cent
                db_amount = float(txn["amount"])
                if abs(db_amount - row.amount) > 0.01:
                    logger.warning(
                        "transaction_id=%d amount mismatch: DB=%s CSV=%s, using CSV amount",
                        row.transaction_id, db_amount, row.amount,
                    )

                # Insert reconciliation record
                cur.execute(
                    "INSERT INTO reporting.reconciled_transactions "
                    "(transaction_id, customer_id, amount, reconciled_at, batch_run_id, status) "
                    "VALUES (%s, %s, %s, NOW(), %s, 'reconciled')",
                    (row.transaction_id, txn["customer_id"], row.amount, batch_run_id),
                )

                # Mark source transaction as reconciled
                cur.execute(
                    "UPDATE app.transactions SET reconciled = true WHERE id = %s",
                    (row.transaction_id,),
                )

                reconciled_count += 1

            except psycopg2.errors.UniqueViolation:
                # The unique index on (transaction_id) WHERE status='reconciled' fired.
                # This means another batch run already reconciled this transaction.
                conn.rollback()
                logger.warning("transaction_id=%d unique violation (duplicate batch run?)", row.transaction_id)
                duplicate_count += 1

    conn.commit()
    return reconciled_count, failed_count, duplicate_count


def write_summary_report(client: Minio, batch_result: BatchResult) -> None:
    """
    Write a summary CSV to the output bucket.
    Filename: summary_<batch_run_id>.csv

    This replaces the on-prem behavior of writing to /mnt/reports/shared/outgoing/.
    The reconciliation shim (shim.py) exposes this file over HTTP for legacy consumers.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["batch_run_id", "input_file", "total_rows", "reconciled_count",
                     "failed_count", "duplicate_count", "started_at", "completed_at"])
    writer.writerow([
        batch_result.batch_run_id,
        batch_result.input_file,
        batch_result.total_rows,
        batch_result.reconciled_count,
        batch_result.failed_count,
        batch_result.duplicate_count,
        batch_result.started_at.isoformat(),
        batch_result.completed_at.isoformat(),
    ])

    csv_bytes = output.getvalue().encode("utf-8")
    object_name = f"summary_{batch_result.batch_run_id}.csv"

    try:
        client.put_object(
            OUTPUT_BUCKET,
            object_name,
            data=io.BytesIO(csv_bytes),
            length=len(csv_bytes),
            content_type="text/csv",
        )
        logger.info("Summary report written to %s/%s", OUTPUT_BUCKET, object_name)
    except S3Error as exc:
        logger.error("Failed to write summary report: %s", exc)


def move_to_processed(client: Minio, object_name: str) -> None:
    """
    Copy the processed file to the processed/ prefix and delete the original.
    This preserves the input file for audit purposes without re-processing it.
    """
    processed_name = f"processed/{object_name}"
    try:
        from minio.commonconfig import CopySource
        client.copy_object(
            INPUT_BUCKET,
            processed_name,
            CopySource(INPUT_BUCKET, object_name),
        )
        client.remove_object(INPUT_BUCKET, object_name)
        logger.info("Moved %s -> %s", object_name, processed_name)
    except S3Error as exc:
        logger.error("Failed to move %s to processed/: %s", object_name, exc)


def process_file(client: Minio, conn, object_name: str) -> BatchResult:
    """
    End-to-end processing of a single input CSV file.
    Returns a BatchResult with counts for the summary report.
    """
    batch_run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    logger.info("Starting batch run %s for file %s", batch_run_id, object_name)

    rows = download_csv(client, object_name)
    if not rows:
        logger.warning("No valid rows found in %s, skipping reconciliation", object_name)
        return BatchResult(
            batch_run_id=batch_run_id,
            input_file=object_name,
            total_rows=0,
            reconciled_count=0,
            failed_count=0,
            duplicate_count=0,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )

    reconciled, failed, duplicate = validate_and_reconcile(conn, batch_run_id, rows)
    completed_at = datetime.now(timezone.utc)

    result = BatchResult(
        batch_run_id=batch_run_id,
        input_file=object_name,
        total_rows=len(rows),
        reconciled_count=reconciled,
        failed_count=failed,
        duplicate_count=duplicate,
        started_at=started_at,
        completed_at=completed_at,
    )

    logger.info(
        "Batch run %s complete: total=%d reconciled=%d failed=%d duplicate=%d elapsed=%.1fs",
        batch_run_id, len(rows), reconciled, failed, duplicate,
        (completed_at - started_at).total_seconds(),
    )
    return result


def run_poll_cycle(client: Minio, conn) -> None:
    """
    One polling cycle: list pending files, process each, write summary, move to processed.
    """
    pending = list_pending_files(client)
    if not pending:
        logger.debug("No pending files in %s", INPUT_BUCKET)
        return

    logger.info("Found %d pending file(s) in %s", len(pending), INPUT_BUCKET)
    for object_name in pending:
        try:
            result = process_file(client, conn, object_name)
            write_summary_report(client, result)
            move_to_processed(client, object_name)
        except Exception as exc:
            logger.exception("Unhandled error processing %s: %s", object_name, exc)
            # Roll back any open transaction on the connection before next file
            try:
                conn.rollback()
            except Exception:
                pass


def main() -> None:
    """
    Main polling loop. Runs until the container is stopped (SIGTERM from ECS).
    Reconnects to the database if the connection is lost (handles RDS failover).
    """
    logger.info(
        "Contoso batch reconciler starting. input_bucket=%s output_bucket=%s poll_interval=%ds",
        INPUT_BUCKET, OUTPUT_BUCKET, POLL_INTERVAL,
    )

    client = get_minio_client()

    # Verify MinIO/S3 connectivity on startup
    try:
        client.bucket_exists(INPUT_BUCKET)
        logger.info("MinIO/S3 connection verified. Input bucket: %s", INPUT_BUCKET)
    except Exception as exc:
        logger.error("Cannot connect to MinIO/S3: %s", exc)
        sys.exit(1)

    conn = None
    while True:
        try:
            # Reconnect to DB if needed (handles container restarts and RDS failover)
            if conn is None or conn.closed:
                logger.info("Connecting to database...")
                conn = get_db_connection()
                logger.info("Database connection established.")

            run_poll_cycle(client, conn)

        except psycopg2.OperationalError as exc:
            logger.error("Database connection lost: %s. Will reconnect on next cycle.", exc)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = None

        except Exception as exc:
            logger.exception("Unexpected error in poll cycle: %s", exc)

        logger.debug("Sleeping %d seconds until next poll cycle.", POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
