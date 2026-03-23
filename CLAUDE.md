# CLAUDE.md

Contoso Financial — Cloud Migration Hackathon (Scenario 2).
Three on-prem workloads migrated to AWS ECS Fargate + RDS + ElastiCache + S3.
Runs fully locally via Docker Compose. Terraform IaC is scaffolded but not applied.

---

## Project Structure

```
contoso-cloud-migration/
├── docs/                         # Challenge write-ups (1–3, 8–10)
│   ├── 01-memo.md                # CTO decision memo — Strangler Fig, refactor on way in
│   ├── 02-discovery.md           # Current state: Python 2.7, hardcoded IPs, NFS coupling
│   ├── 03-options.md             # Three AWS options scored; ECS Fargate chosen (18/20)
│   ├── 08-cost-model.md          # On-prem $114K/yr → AWS Y1 $3.1K → Y2 $2.6K
│   ├── 09-dr-plan.md             # Warm standby us-west-2, RTO 15min, RPO 5min
│   └── 10-rollback.md            # Per-workload rollback at each migration stage
├── workloads/
│   ├── webapp/                   # Challenge 4 — Flask 3.0, multi-stage Dockerfile
│   ├── batch-reconciliation/     # Challenge 5 — event-driven worker + shim
│   └── reporting-db/             # init.sql — schemas, cross-schema view, seed data
├── infra/terraform/              # Challenge 6 — VPC, ECS, RDS, ElastiCache, S3, IAM
├── tests/
│   ├── smoke/                    # Challenge 7 — 12 smoke tests
│   ├── contract/                 # Challenge 7 — 11 contract tests
│   └── data_integrity/           # Challenge 7 — 12 data integrity tests
├── docker-compose.yml            # Full local stack (MinIO=S3, Postgres=RDS, Redis=ElastiCache)
├── .env.example                  # All env vars with AWS equivalents documented
└── presentation.html             # 7-slide hackathon presentation
```

---

## Quick Start

```bash
# Prerequisites: Docker Desktop, Python 3.11+

# 1. Configure environment
cp .env.example .env

# 2. Start the full local stack
docker compose up -d

# Wait ~30s for services to become healthy
docker compose ps

# 3. Verify web app is running
curl http://localhost:8080/health

# 4. Run all tests
pip install pytest requests psycopg2-binary redis boto3
python -m pytest tests/ -v

# 5. Open MinIO console (S3 equivalent)
# http://localhost:9001  →  user: minioadmin / pass: minioadmin123

# 6. Tear down
docker compose down -v
```

---

## Service Ports (Local)

| Port | Service | AWS Equivalent |
|---|---|---|
| 8080 | Web app (Flask) | ECS Fargate + ALB |
| 8081 | Batch shim (FastAPI) | ECS Fargate (shim task) |
| 5432 | PostgreSQL 15 | RDS PostgreSQL 15 |
| 6379 | Redis 7 | ElastiCache Redis 7 |
| 9000 | MinIO S3 API | Amazon S3 |
| 9001 | MinIO console | AWS Console |

---

## Architecture

```
Route 53 (weighted → failover)
    │
    ALB ──────────────────────────────────┐
    │                                     │
ECS Fargate (webapp)              ECS Fargate (batch-worker)
Flask 3.0 / Python 3.11           Event-driven on S3 file arrival
    │                                     │
    ├── RDS Postgres 15 (Multi-AZ)        │
    │       └── app + reporting schemas   │
    ├── ElastiCache Redis 7               │
    └── SSM Parameter Store       S3 (reconciliation-input/)
                                         │
                                  Batch Shim (HTTP → legacy NFS API)
```

**DR:** Warm standby in us-west-2. Route 53 auto-failover on health check failure.

---

## Key Design Decisions

1. **No lift-and-shift.** Refactor on the way in (Strangler Fig). Python 2.7 → 3.11, cron → event-driven.
2. **Kill the cron job.** S3 event notifications replace the 2am schedule. Files arrive → worker triggers immediately.
3. **Compatibility shim.** Legacy consumers that read `/mnt/reports/shared` get an HTTP façade. Zero downstream code changes.
4. **Honest about unknowns.** The `10.0.1.45` ledger API is an undocumented dependency — stubbed and flagged, not silently ignored.
5. **Tests define "done".** Same test code runs local (Docker Compose) and against AWS (swap `WEBAPP_URL` env var).

---

## Known Gaps (Intentional)

- **Terraform is scaffolded, not applied.** IaC reads correctly but needs a real AWS account. `data.tf` has a TODO for the unknown ledger API.
- **Batch worker polls MinIO locally.** In AWS it should receive S3 event notifications via SQS. Architecture is designed for it; the code has a comment where the SQS consumer would go.
- **`10.0.1.45` ledger stub.** The internal ledger service has no docs, no service name, and may not exist. It's stubbed in the app and flagged in docs/02-discovery.md.
- **No Secrets Manager rotation.** SSM Parameter Store is wired up; rotation is the next security improvement.
- **us-west-2 Terraform not written.** The DR plan (docs/09-dr-plan.md) is complete; the standby region IaC module isn't.

---

## Test Environment Variables

Tests read from environment — no hardcoded URLs:

```bash
# Local (default)
export WEBAPP_URL=http://localhost:8080
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=contoso
export DB_USER=contoso
export DB_PASSWORD=contoso_secret
export REDIS_HOST=localhost
export REDIS_PORT=6379
export MINIO_ENDPOINT=http://localhost:9000
export MINIO_ACCESS_KEY=minioadmin
export MINIO_SECRET_KEY=minioadmin123

# AWS post-cutover — swap these values, run the same tests
export WEBAPP_URL=https://<alb-dns>
export DB_HOST=<rds-endpoint>
# ... etc
```

---

## Coding Standards

- Always document non-obvious logic changes with comments
- Non-root users in all Dockerfiles (appuser uid 1001, batchuser uid 1002)
- No hardcoded secrets — all credentials via environment variables or SSM
- Health checks on all long-running containers
- Multi-stage Dockerfiles to minimize image size

---

## Troubleshooting

**Tests fail immediately with connection errors**
→ Stack isn't ready. Run `make up`, wait 30s, retry.
→ Check: `docker compose ps` — all services should show `healthy`.

**MinIO bucket not found (`reconciliation-input`)**
→ The `minio-init` container may have failed. Check: `docker compose logs minio-init`.
→ Fix: `docker compose restart minio-init`.

**Postgres schema missing (`app` or `reporting`)**
→ Init SQL didn't run. Check: `docker compose logs rds-postgres | grep ERROR`.
→ Fix: `make down && make up` (volumes are wiped, init re-runs on fresh start).

**Batch worker not processing files**
→ Check `POLL_INTERVAL` in docker-compose.yml (default 30s). Upload a CSV, wait 30s.
→ Check logs: `docker compose logs contoso-batch`.
→ Verify the file landed in the right bucket: MinIO console → `reconciliation-input/`.

**Terraform validate fails**
→ Run `terraform init -backend=false` first — providers must be initialized.
→ The `data.tf` TODO for the ledger API is an intentional known gap, not an error.

---

## Makefile

Common operations available via `make`:

```bash
make up              # Start Docker Compose stack
make down            # Tear down + remove volumes
make test            # Run all 3 test suites
make test-smoke      # Smoke tests only (fastest feedback)
make validate        # Terraform fmt + validate + Compose + Python syntax
make build           # Rebuild Docker images (no cache)
make trigger-batch   # Upload a test CSV to trigger the batch worker
make clean           # Remove containers, caches, .pyc files
make help            # Show all targets
```

---

## Custom Skills (Slash Commands)

These are available as Claude Code slash commands from `.claude/commands/`:

| Command | What it does |
|---|---|
| `/test` | Checks stack health, then runs all 3 test suites with env var guidance |
| `/up` | Starts Docker Compose, waits for health, prints all endpoints |
| `/validate` | Runs Terraform fmt/validate + Compose config + Python syntax checks |
| `/cutover` | Pre-cutover gate checklist — automated + manual checks before shifting traffic |

---

## Hooks

Configured in `.claude/settings.json`. Fire automatically during Claude Code sessions:

| Event | Trigger | Action |
|---|---|---|
| `PostToolUse` | Edit/Write any `.py` file | Runs `python3 -m py_compile` — catches syntax errors immediately |
| `PostToolUse` | Edit/Write any `.tf` file | Runs `terraform fmt -check` — flags unformatted HCL |
| `Stop` | End of any Claude session | Reminds to run `make test` / `make validate` / `make down && make up` |

---

## Subagents

| Task | Agent | Why |
|---|---|---|
| Modifying workload Python code | **code-reviewer** | Catches security issues, missing error handling, N+1 queries |
| Understanding cross-workload dependencies | **Explore** | Maps how batch worker → DB → shim interact without reading every file |
| Terraform refactors or multi-file changes | **general-purpose** | Keeps consistent variable naming and avoids drift between modules |
| Writing or updating test suites | **general-purpose** | Ensures conftest fixtures and test helpers stay consistent across suites |
| Docker or CI/CD changes | **code-reviewer** | Validates non-root users, health checks, and secret handling |

### MCP Tools
- **ALWAYS use GitHub MCP tools** (`mcp__github__*`) for ALL GitHub operations
  - Exception: Local branches only — use `git checkout -b` instead
