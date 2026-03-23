# Current State Discovery: Contoso Financial Workloads

**Document type:** Pre-migration current state assessment
**Conducted by:** Platform Engineering Team
**Date:** March 2026
**Status:** FINAL - approved for migration planning

---

## Overview

This document records the actual state of the three workloads targeted for migration. It combines findings from code inspection, infrastructure interviews, and runtime observation. It is intentionally unpolished -- it reflects what we found, not what the architecture diagrams say should exist.

---

## Workload 1: Customer-Facing Web Application

### Runtime
- Python **2.7.18** (EOL January 2020)
- Flask 1.0.2
- Serving via Apache mod_wsgi on Ubuntu 16.04 LTS (also EOL)
- No process supervisor; application is started manually by a cron @reboot entry

### Dependencies

| Dependency | Location | Notes |
|---|---|---|
| PostgreSQL | 10.0.1.45:5432 | Hardcoded IP. See undocumented findings. |
| Redis | localhost:6379 | Session cache. No auth token. |
| Shared filesystem | /mnt/reports/shared | NFS mount from the file server. |
| Internal ledger API | http://10.0.1.45:8443/ledger/v1 | Hardcoded IP, no service name, no docs. |

### Configuration

Configuration is read from `/opt/contoso/webapp/config.ini`. The file contains hardcoded database host `10.0.1.45`, database password `C0ntos0App2019!`, and a static Flask secret key `contoso-flask-secret-2019`. There are **no environment variables** used anywhere in the application. All configuration is file-based.

### Routes of Note
- `GET /` -- Dashboard with customer count and recent transaction table
- `GET /internal/report-export` -- Reads files directly from `/mnt/reports/shared/exports/` and serves them as HTTP downloads. If the NFS mount is unavailable, this endpoint hangs for 30 seconds before returning a 500 error.
- `GET /customers` -- Returns paginated customer list
- `POST /transactions` -- Creates a transaction record

### Known Issues
- No connection pooling. Each request opens and closes a DB connection.
- The Redis client is initialized at module load time; if Redis is unavailable at startup, the entire application crashes.
- Session data is never expired. Redis memory grows unboundedly.
- Python 2.7 urllib2 is used for the internal ledger API call with no timeout set.
- The `/internal/report-export` route has no authentication. Anyone with network access can download all report files.

---

## Workload 2: Nightly Batch Reconciliation Job

### Runtime
- Shell wrapper script: `/opt/contoso/batch/run_reconciliation.sh`
- Invokes: Python **3.6.9** script `/opt/contoso/batch/reconciler.py`
- Scheduled via system cron: `0 2 * * *` (runs at 02:00 local time every night)
- Runs on the **database server** (same host as Postgres -- this was "temporary" in 2019)

### Cron Entry

```
0 2 * * * postgres /opt/contoso/batch/run_reconciliation.sh >> /var/log/contoso/batch.log 2>&1
```

Note: the cron user is **postgres** (the OS user). The Python script inherits the postgres OS user context and connects to the database as the PostgreSQL superuser. See undocumented findings.

### Shell Wrapper Notes

The shell wrapper begins with `sleep 30` to wait for the NFS mount to be ready. The sleep exists because the NFS mount occasionally takes 20-25 seconds to become available after a network blip. This is not monitored. If the mount takes longer than 30 seconds, the job silently reads zero input files and writes nothing, then exits successfully with a zero exit code.

The wrapper calls the Python script with arguments: `--input /mnt/reports/shared/incoming/ --output /mnt/reports/shared/outgoing/ --db-host 10.0.1.45 --db-user postgres --db-pass ""`

### Data Flow
1. Reads CSV files from `/mnt/reports/shared/incoming/` (files placed here by an upstream bank feed process, not documented anywhere)
2. Parses each row: transaction_id, amount, timestamp, source_system
3. Looks up each transaction_id in the app.transactions table
4. Writes results directly to the reporting.reconciled_transactions table
5. Updates app.transactions SET reconciled = true
6. Writes a summary CSV to `/mnt/reports/shared/outgoing/reconciliation_YYYYMMDD.csv`
7. Does **not** call any web application API. Writes directly to the database.

### Known Issues
- If the job fails mid-run, there is no rollback. Partial reconciliation data is committed to the DB.
- No idempotency. Running the job twice on the same input files creates duplicate rows in reporting.reconciled_transactions.
- Log file rotation is not configured. `/var/log/contoso/batch.log` is 4.7 GB as of this assessment.
- Python 3.6 is EOL. Several dependencies have known CVEs.
- The empty `--db-pass ""` argument means the job relies on `.pgpass` file or `trust` auth in `pg_hba.conf`.

---

## Workload 3: Reporting Database

### Runtime
- PostgreSQL **11.18** on Ubuntu 18.04 LTS (both EOL)
- Same physical host as the production application database (db-server-01, 10.0.1.45)
- No connection pooler (PgBouncer, pgpool-II, etc.)
- No read replicas

### Schema Structure

Two schemas on the same database instance:
- `app` schema: owned by `contoso_app` user, contains `customers` and `transactions` tables
- `reporting` schema: owned by `postgres` superuser, contains `reconciled_transactions` table and several views

### Cross-Schema Dependency (CRITICAL)

The reporting schema contains views that JOIN across both schemas. The `reporting.customer_reconciliation_summary` view JOINs `app.customers` with `reporting.reconciled_transactions`. This cross-schema JOIN means the app and reporting schemas **cannot be separated** without breaking all five BI teams Metabase connections. Both schemas must migrate together, or the views must be rewritten before migration.

### Access Control

Five teams have direct database access. No connection pooler is in place.

| Team | Role | Access Level |
|---|---|---|
| Finance Ops | reporting_readonly | SELECT on reporting schema |
| Audit and Compliance | reporting_readonly | SELECT on reporting schema |
| Risk Management | reporting_readonly | SELECT on reporting schema |
| Executive Reporting | bi_user | SELECT on reporting schema and summary views |
| Data Engineering | contoso_app | Full access to app schema |

### BI Tool Configuration

Three separate Metabase instances (Finance, Audit, Executive) are pointed directly at `10.0.1.45:5432`. These connection strings are configured in each Metabase's application.properties file and are not centrally managed.

---

## Undocumented Findings

These items were not in any architecture diagram, runbook, or ticket. They were discovered during direct inspection.

### Finding 1: The Shared Filesystem Is the Hidden Integration Bus

`/mnt/reports/shared` is the actual integration point between all three workloads:

- The web application reads export files from it directly via the `/internal/report-export` endpoint
- The batch job reads its input from `incoming/` and writes output to `outgoing/`
- An upstream bank feed process (out of scope for this migration) places CSV files in `incoming/`

None of this is documented. The NFS server is a physical appliance (NAS-01) in the server room. It does not have a backup. It has been running without a reboot since 2021. **The shared filesystem is the single point of failure for the entire batch pipeline.**

Mitigation path: Replace with S3. All consumers need to be updated to read/write from S3 instead of filesystem paths.

### Finding 2: The Hardcoded IP 10.0.1.45

`10.0.1.45` appears in: config.ini (database host), run_reconciliation.sh (--db-host argument), the web app ledger API call (http://10.0.1.45:8443/ledger/v1), and three Metabase connection strings.

The database is confirmed to be on this IP. The **ledger API** on port 8443 is unconfirmed. We queried three senior engineers and none of them know what service listens on port 8443. A port scan confirms the port is open and responds to HTTPS. The certificate is self-signed and expired in 2022. **We do not know if this service can be decommissioned or if it has active consumers beyond the web app.**

Action required: Before any cutover, the ledger API on 10.0.1.45:8443 must be identified and either replaced with a proper endpoint or confirmed decommissioned. This is a **migration blocker**.

### Finding 3: The Batch Job Runs as the PostgreSQL Superuser

The cron entry runs as the `postgres` OS user. The PostgreSQL superuser on this instance uses ident authentication from the OS (no password required). The Python reconciler script therefore connects as a superuser with unrestricted access to all schemas and tables.

This means: there is no audit trail separating batch writes from administrative changes; a bug in the reconciler could accidentally DROP or TRUNCATE production tables; and in AWS RDS, the rds_superuser equivalent requires a password -- the current authentication mechanism will not work without modification.

Remediation required before migration: create a dedicated `batch_reconciler` database role with only the necessary INSERT and UPDATE permissions on the relevant tables.
