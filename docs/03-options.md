# Target Architecture Options: AWS Migration

**Document type:** Options analysis
**Prepared by:** Platform Engineering Team
**Date:** March 2026

---

## Scoring Criteria

Each option is scored 1-5 on four dimensions:
- **Cost**: 5 = lowest cost, 1 = highest cost
- **Risk**: 5 = lowest risk, 1 = highest risk
- **Speed**: 5 = fastest to deliver, 1 = slowest
- **Operability**: 5 = easiest to operate long-term, 1 = hardest

---

## Option A: Lift-and-Shift to EC2

### Architecture Description

Move each workload to an equivalent EC2 instance. The web application runs on an EC2 instance (t3.medium) behind an Application Load Balancer. The batch job runs on a separate EC2 instance as a cron job, using the same shell script and cron schedule as today. The NFS mount is replaced with Amazon EFS (Elastic File System), which is mountable from EC2 -- this preserves the `/mnt/reports/shared` integration pattern with minimal code changes. The database migrates to RDS PostgreSQL 15 using AWS DMS. Redis migrates to ElastiCache.

The web application code is largely unchanged. Configuration remains in a config file, but the file is baked into an AMI or pulled from S3 at instance boot. Credentials are not yet moved to Secrets Manager.

### AWS Services Used

- EC2 (t3.medium for web, t3.small for batch)
- Application Load Balancer
- Amazon EFS (replaces NFS)
- Amazon RDS PostgreSQL 15 (Multi-AZ)
- Amazon ElastiCache Redis
- Amazon CloudWatch (basic metrics only)
- AWS DMS (for initial data migration)

### Score

| Dimension | Score | Rationale |
|---|---|---|
| Cost | 2 | EC2 on-demand is expensive. EFS adds ~$0.30/GB/mo. Reserved instances require upfront commitment before workload is profiled. |
| Risk | 3 | Low code change risk, but EFS mount timing issues may reproduce the `sleep 30` problem. Python 2.7 still EOL. Credentials still in config files. |
| Speed | 5 | Fastest to deliver. Mostly infrastructure change, minimal code change. |
| Operability | 2 | EC2 requires patching, AMI management, and SSH access governance. EFS adds a stateful mount dependency. Scaling requires manual AMI baking or complex user-data scripts. |

### Pros
- Fastest path to "running in AWS" -- achievable in 3-4 weeks
- Minimal code changes required
- Team already understands the operational model (it is the same as on-prem)

### Cons
- Does not resolve the shared filesystem coupling -- EFS recreates the same single-point-of-failure pattern in the cloud
- Does not resolve the hardcoded IP problem (still requires manual config file management)
- Python 2.7 on EC2 is still EOL and unsupported
- No benefit from cloud-native scaling, health checks, or zero-downtime deploys
- EC2 instance management (patching, AMI lifecycle, SSH key rotation) creates ongoing operational burden
- Accumulates cloud-native debt: a second, harder migration will be required within 18 months

---

## Option B: Containerized on ECS Fargate (RECOMMENDED)

### Architecture Description

Each workload is containerized using Docker. The web application runs as an ECS Fargate task behind an Application Load Balancer, with 2 tasks for availability. The batch reconciliation job runs as a separate ECS Fargate task, triggered by S3 Event Notifications routed through SQS -- eliminating the cron schedule entirely. Files that previously lived on NFS are stored in S3. The reporting database migrates to RDS PostgreSQL 15. Redis session cache migrates to ElastiCache.

Configuration is externalized from config files to environment variables injected at task startup from SSM Parameter Store (SecureString). Credentials are never baked into images. The cross-schema view dependency in the reporting schema is preserved -- both app and reporting schemas coexist in the same RDS instance, so no view rewriting is required at migration time.

The compatibility shim (`shim.py`) runs as a sidecar ECS service, exposing an HTTP API over the S3 output bucket so legacy consumers that expected files at `/mnt/reports/shared/outgoing/` can migrate to HTTP at their own pace without blocking the batch job migration.

### AWS Services Used

- Amazon ECS (Fargate launch type)
- Application Load Balancer
- Amazon ECR (container image registry)
- Amazon RDS PostgreSQL 15 (Multi-AZ, db.t3.medium)
- Amazon ElastiCache Redis (cache.t3.micro)
- Amazon S3 (replaces NFS for reconciliation files)
- Amazon EventBridge (S3 event routing to SQS)
- Amazon SQS (batch trigger queue)
- AWS SSM Parameter Store (SecureString for credentials)
- Amazon CloudWatch (Logs, Metrics, Alarms)
- AWS IAM (task roles, least-privilege policies)
- Amazon Route 53 (DNS failover for DR)

### Score

| Dimension | Score | Rationale |
|---|---|---|
| Cost | 4 | Fargate eliminates EC2 instance management. Pay per task-hour. No idle capacity cost for batch worker. Total ~$337/mo vs $6,500/mo on-prem. |
| Risk | 4 | Containerization requires code changes but the changes are well-understood. Cross-schema dependency preserved in same RDS instance avoids a high-risk schema refactor. |
| Speed | 3 | 6-8 weeks. Longer than Option A due to containerization work, but the parallel run period is shorter because there are fewer unknowns at cutover. |
| Operability | 5 | Fargate eliminates instance patching. CloudWatch integration is native. Blue-green deployments via ECS are straightforward. Auto-scaling is built in. |

### Pros
- Eliminates the shared filesystem coupling (S3 replaces NFS)
- Eliminates hardcoded credentials (SSM Parameter Store)
- Batch job becomes event-driven -- no more fragile cron + sleep(30) pattern
- Containers are portable: the same image tested locally runs in production
- Fargate scales to zero for the batch worker between jobs (cost savings)
- Sets the foundation for multi-region DR (Option B is the basis for the DR plan in doc 09)
- No EC2 instance patching, no AMI lifecycle management

### Cons
- Requires containerization work on three workloads (estimated 3-4 engineer-weeks)
- Team needs ECS/Fargate training (budgeted -- see memo doc 01)
- Two environments to maintain during the parallel run period (8 weeks)
- The hardcoded ledger API IP is still a blocker (see below)

---

## Option C: Full Serverless

### Architecture Description

The web application is decomposed into Lambda functions behind API Gateway. Each route becomes a Lambda function. The batch reconciliation job becomes a Lambda triggered by S3 PutObject events. The reporting database moves to Aurora Serverless v2 (PostgreSQL-compatible). Session caching moves to ElastiCache (Lambda functions can reach it within a VPC), or is replaced with DynamoDB TTL-based session storage.

### AWS Services Used

- AWS Lambda
- Amazon API Gateway (HTTP API)
- Amazon Aurora Serverless v2 (PostgreSQL-compatible)
- Amazon S3
- Amazon DynamoDB (session store)
- Amazon SQS
- Amazon CloudWatch
- AWS X-Ray (distributed tracing)

### Score

| Dimension | Score | Rationale |
|---|---|---|
| Cost | 5 | Near-zero idle cost. Lambda pricing is per-invocation. Aurora Serverless scales to zero. Most cost-efficient at scale. |
| Risk | 1 | Highest risk. Requires full application decomposition. Cold start latency affects user-facing performance. The cross-schema DB view dependency must be rewritten. |
| Speed | 1 | Slowest. Full application rewrite. Estimated 4-6 months minimum. |
| Operability | 4 | Serverless operations are simpler once the architecture is established, but debugging distributed Lambda functions is significantly harder than debugging a containerized Flask app. |

### Pros
- Lowest long-term cost at scale
- No servers or containers to manage
- Lambda scales automatically with zero configuration

### Cons
- **Requires full application rewrite** -- this is not a migration, it is a rebuild
- The `reporting.customer_reconciliation_summary` cross-schema view (identified in Discovery) JOINs app and reporting schemas that must remain co-located in the same database instance. Aurora Serverless v2 can accommodate this, but the migration complexity multiplies significantly because the application layer must be completely decomposed before the view dependencies can be rationalized
- Lambda cold starts (100-500ms) affect the customer-facing web application user experience without significant architectural investment in provisioned concurrency
- Out of scope for the current migration timeline. Revisit in Year 2.

---

## Recommendation: Option B

### Rationale

Option B is the right architecture for this migration. It resolves the three critical undocumented findings from the Discovery document without requiring a full application rewrite:

1. **Shared filesystem coupling** is eliminated by replacing NFS with S3.
2. **Hardcoded credentials** are eliminated by moving configuration to SSM Parameter Store.
3. **Batch job fragility** is eliminated by replacing the cron + sleep(30) pattern with S3 Event Notifications driving an ECS task.

Option A (lift-and-shift) would preserve all three problems inside AWS, creating compounding technical debt. Option C (full serverless) would require rewriting the application from scratch, which is not justified by the current business case and would take longer than the migration timeline allows.

### Blocking Issue: The Internal Ledger API

The hardcoded IP `10.0.1.45:8443` for the internal ledger service is a **blocker for all three options**. Before any cutover can proceed, the ledger service must be:

1. Identified (which team owns it, what it does, whether it is still in use)
2. Either: exposed via a proper DNS name and registered in Route 53 or AWS PrivateLink, OR confirmed decommissioned and removed from the web application codebase

This work is not included in the Option B timeline estimate. It must be tracked as a separate workstream with a dedicated owner. If it is not resolved before the parallel run period begins, the web application cannot be cut over to AWS.

### Migration Sequence for Option B

1. Containerize web application (Python 3.11 upgrade included)
2. Containerize batch reconciliation job (replace cron with polling loop; S3 input/output)
3. Deploy RDS and ElastiCache; migrate data using pg_dump/pg_restore
4. Deploy ECS cluster, push images to ECR, configure SSM parameters
5. Run parallel: on-prem and AWS simultaneously, Route 53 weighted routing at 10% AWS
6. Resolve ledger API blocker
7. Cut over to 100% AWS traffic
8. Run data integrity tests (doc 21)
9. Decommission on-prem after 72-hour stability window
