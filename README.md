# Team Contoso Cloudbusters

## Participants
- Tripti Goel

## Scenario
Scenario 2: Cloud Migration — *"The Lift, the Shift, and no Regrets"*

---

## What We Built

Three hours ago, Contoso Financial's infrastructure existed only in on-prem servers, a 2am cron job, and the collective anxiety of a CTO and CFO who aren't aligned. What exists in this repo now is a cloud-ready migration stack targeting AWS — fully runnable locally via Docker Compose, where MinIO stands in for S3, Postgres for RDS, and Redis for ElastiCache. The same containers, without modification, are ready to be pushed to ECR and deployed on ECS Fargate.

The centerpiece is the architectural decision we made and defended: no pure lift-and-shift. The web app is containerized with a multi-stage Dockerfile (non-root user, health check, gunicorn), the batch reconciliation job has been killed as a 2am cron and reborn as an event-driven worker that triggers on file arrival in object storage, and the reporting database is documented — including the cross-schema view dependency and the five BI teams with direct Postgres access that nobody wrote down. That undocumented coupling shows up as a gap in the Terraform and as a blocker in the options analysis, because it should.

What's real: the Docker Compose environment runs end-to-end, the Flask web app serves traffic with DB and Redis wired up, the batch worker polls MinIO and writes reconciliation results to Postgres, and the pre/post-cutover test suite (smoke, contract, data integrity) runs against the local stack. What's scaffolding: the Terraform reads right but hasn't been applied to a live AWS account. What's faked: the internal ledger service at `10.0.1.45` — we documented it in Discovery, flagged it as a blocker in Options, and left a stub in the app. Nobody knows if it still exists.

---

## Challenges Attempted

| # | Challenge | Status | Notes |
|---|---|---|---|
| 1 | The Memo | Done | CTO decision memo: refactor on the way in, Strangler Fig pattern. Took a side. |
| 2 | The Discovery | Done | All three workloads documented. Hardcoded IP, shared NFS mount, batch→DB direct write found and called out. |
| 3 | The Options | Done | Three AWS architectures scored. Option B (ECS Fargate) recommended. Ledger IP flagged as cross-cutting blocker. |
| 4 | The Container | Done | Multi-stage Dockerfile, non-root user, health check. ECS push commands documented in Dockerfile header. |
| 5 | The Rewire | Done | 2am cron replaced with MinIO-polling event-driven worker. Compatibility shim exposes old filesystem interface over HTTP. |
| 6 | The Foundation | Partial | Terraform for VPC, ECS, RDS, ElastiCache, S3, IAM. Reads right, not applied. Cross-schema dependency surfaces as a gap in `data.tf`. |
| 7 | The Proof | Done | Smoke, contract, and data integrity tests. Same test code runs local and against AWS post-cutover via env var swap. |
| 8 | The Bill | Done | On-prem $78K/yr vs AWS Year 1 $4K/yr vs Year 2 $2.6K/yr. Assumptions stated. Reserved instance and Fargate Spot optimization path shown. |
| 9 | The Disaster | Done | Warm standby in us-west-2. RTO 15min, RPO 5min. Numbered failover runbook. |
| 10 | The Undo | Done | Per-workload rollback at each migration stage. Includes deduplication query for mid-cutover batch collision. Rollback window defined. |

---

## Key Decisions

**AWS over Azure/GCP.** The scenario's own hints (ECS, S3, ElastiCache) pointed here. More importantly, ECS Fargate gave us the best ratio of operational simplicity to cloud-nativeness for a team that isn't Kubernetes-native.

**ECS Fargate over EKS.** EKS is the right answer at scale; it's the wrong answer for a two-person migration team with a 3-hour clock. Fargate removes cluster management entirely. We can migrate to EKS in Year 2 once the team has upskilled.

**Event-driven batch over cron.** The 2am cron was a symptom of a polling architecture bolted onto a shared filesystem. S3 event notifications are free, atomic, and self-documenting. The compatibility shim (Challenge 5) lets downstream consumers keep working without code changes.

**Document the mess before fixing it.** Challenge 2 was deliberately written with the hardcoded IP and cross-schema dependency intact. These show up as a blocker in Challenge 3, a gap in Challenge 6, and a rollback risk in Challenge 10. The architecture is honest about what it can't solve in one sprint.

---

## How to Run It

```bash
# Prerequisites: Docker Desktop, Docker Compose v2, Python 3.11+

# 1. Clone and configure
cd contoso-cloud-migration
cp .env.example .env

# 2. Start the full local stack
docker compose up -d

# Wait for services to be healthy (~30s)
docker compose ps

# 3. Run the test suite
pip install pytest requests psycopg2-binary redis boto3
python -m pytest tests/ -v

# 4. Hit the web app
curl http://localhost:8080/health
curl http://localhost:8080/api/customers

# 5. Trigger a reconciliation run manually
# Upload a CSV to MinIO:
docker compose exec minio-init \
  mc cp /dev/stdin s3-minio/reconciliation-input/test-run.csv << 'EOF'
transaction_id,amount,timestamp,source_system
1,150.00,2026-03-23T09:00:00,CORE_BANKING
2,275.50,2026-03-23T09:01:00,CORE_BANKING
EOF
# The batch worker picks it up within 30s (POLL_INTERVAL default)

# 6. MinIO console (S3 equivalent)
open http://localhost:9001  # user: minioadmin / pass: minioadmin123

# 7. Tear down
docker compose down -v
```

---

## If We Had Another Day

1. **Apply the Terraform.** The IaC reads right but needs a real AWS account to prove it. First thing we'd do is `terraform apply` against a sandbox account and fix whatever the plan surfaces.

2. **Wire S3 → SQS → ECS** for the batch worker. Right now it polls MinIO. In production it should receive S3 Event Notifications via SQS. The architecture is designed for it; the code has a comment where the SQS consumer would go.

3. **Resolve the `10.0.1.45` ledger dependency.** Right now it's a stub. In a real engagement this is a two-week discovery workstream — find the service, document its API, decide whether to lift it alongside or mock it behind an API Gateway.

4. **Multi-region Terraform.** The DR plan (Challenge 9) calls for a warm standby in us-west-2. The runbook is written; the `us-west-2` Terraform module isn't.

5. **Secrets rotation.** SSM Parameter Store is wired up but we're not using Secrets Manager with automatic rotation. That's the first security debt to pay.

6. **Actually upgrade the web app from Python 2.7.** Discovery documented it; the containerized version runs 3.11. The compatibility tests cover the API contract, but there are almost certainly runtime differences in edge cases that only show in production load.

---

## How We Used Claude Code

**What worked best:** The discovery document (Challenge 2). Describing a realistic, messy current state — the hardcoded IP, the shared mount, the superuser batch job — and having it thread through every subsequent artifact (blocking Challenge 3, surfacing in Challenge 6, appearing in the rollback plan) was something that would have taken hours to keep consistent manually. Claude kept the narrative coherent across 10 separate documents and code files.

**What surprised us:** The test suite (Challenge 7). Writing data integrity tests that explicitly define "migration succeeded" — and noting in the module docstring that the same test code runs local and against AWS by swapping a single env var — felt like a real deliverable rather than a checkbox. That framing came from pushing on what "proof" actually means.

**Where it saved the most time:** Scaffolding the Terraform. VPC, subnets, security groups, ECS task definitions, RDS with Multi-AZ, ElastiCache, IAM roles — that's 4-6 hours of reference-checking on a normal day. Having it generated in the right structure with sensible defaults (no hardcoded secrets, idempotent, variables for everything) meant we could spend time on the decisions rather than the syntax.

**Honest note:** The agent call to generate all 25 files at once was too much to review in a hackathon setting. We broke it into focused chunks — docs first, then infra, then code — which made the output reviewable and caught issues before they compounded.
