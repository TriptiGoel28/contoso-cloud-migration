# Challenge 9 — The Disaster: DR Plan

**Contoso Financial — Disaster Recovery Plan**
*Architecture: Warm Standby, us-east-1 (primary) → us-west-2 (standby)*

---

## Recovery Objectives

| Objective | Target | How Achieved |
|---|---|---|
| **RTO** (Recovery Time Objective) | **15 minutes** | Route 53 auto-failover (60s TTL) + ECS warm standby + RDS read replica promotion (2-3 min) |
| **RPO** (Recovery Point Objective) | **5 minutes** | RDS automated backups every 5 min + continuous binlog replication to read replica |

---

## Architecture Overview

```
                    Route 53 (Failover Routing Policy)
                    Health Check on us-east-1 ALB
                           │
              ┌────────────┴────────────┐
              │ PRIMARY (us-east-1)     │   STANDBY (us-west-2)
              │                         │
              │ ALB → ECS (2 tasks)     │   ALB → ECS (1 task, warm)
              │        ↓                │          ↓
              │       RDS (primary) ────┼──────► RDS (read replica)
              │        ↓                │
              │    ElastiCache          │   ElastiCache (separate)
              │        ↓                │
              │       S3 ──────────────┼──────► S3 (cross-region replication)
              └─────────────────────────┘
```

**Key design decisions:**
- Route 53 health checks poll the ALB `/health` endpoint every 30s (3 failures = failover)
- ECS in us-west-2 runs at `desired_count=1` (warm, not hot) to save cost — scales up post-failover
- RDS read replica in us-west-2 has <5min replication lag in steady state
- S3 Cross-Region Replication (CRR) keeps reconciliation files in sync
- ElastiCache is NOT replicated — session data is ephemeral, users re-authenticate after failover

---

## What Breaks First

1. **Route 53 health check fails** — ALB endpoint unreachable or returning 5xx
2. After 3 consecutive failures (~90s), Route 53 switches DNS to us-west-2 ALB
3. During the 60s TTL flush, ~1 minute of requests may fail or be routed to unhealthy target
4. **Sessions are lost** — ElastiCache is not replicated. Users see a login prompt.
5. **In-flight batch runs may be incomplete** — any reconciliation job mid-run in us-east-1 is abandoned. Check for partial writes post-failover.

---

## Automated vs Manual Steps

| Step | Automated? | Trigger |
|---|---|---|
| Route 53 DNS failover | ✅ Automatic | Health check fails 3× |
| CloudWatch alarm + SNS notification | ✅ Automatic | ALB UnhealthyHostCount > 0 for 5 min |
| RDS read replica promotion | ❌ Manual | On-call engineer |
| SSM Parameter update | ❌ Manual | On-call engineer |
| ECS redeployment in us-west-2 | ❌ Manual (or can be automated) | On-call engineer |

---

## Failover Runbook

**Pre-requisite:** You have been paged by the CloudWatch alarm `contoso-primary-alb-unhealthy`. The incident commander has been assigned.

**Step 1 — Verify this is real, not a false positive**
```bash
# Check CloudWatch alarm state
aws cloudwatch describe-alarms --alarm-names contoso-primary-alb-unhealthy --region us-east-1

# Hit the ALB directly (bypassing Route 53)
curl -v http://<us-east-1-alb-dns>/health

# Check ECS service events
aws ecs describe-services --cluster contoso-prod --services contoso-webapp --region us-east-1
```

**Step 2 — Declare incident**
```
Notify: #incidents Slack channel
Page: on-call DBA (for RDS promotion)
Incident Commander: [name]
Start time: [timestamp]
```

**Step 3 — Verify Route 53 has switched (or trigger manually)**
```bash
# Check if DNS has switched
dig api.contoso.internal

# If not yet switched, force failover:
aws route53 change-resource-record-sets --hosted-zone-id <zone-id> \
  --change-batch file://failover-override.json --region us-east-1
```

**Step 4 — Promote RDS read replica in us-west-2** *(~2-3 minutes)*
```bash
aws rds promote-read-replica \
  --db-instance-identifier contoso-migration-prod-db-replica-usw2 \
  --region us-west-2

# Wait for promotion to complete
aws rds wait db-instance-available \
  --db-instance-identifier contoso-migration-prod-db-replica-usw2 \
  --region us-west-2

# Get the new endpoint
aws rds describe-db-instances \
  --db-instance-identifier contoso-migration-prod-db-replica-usw2 \
  --region us-west-2 \
  --query 'DBInstances[0].Endpoint.Address'
```

**Step 5 — Update SSM Parameter with new DB endpoint**
```bash
NEW_DB_ENDPOINT="<output from step 4>"
aws ssm put-parameter \
  --name /contoso/prod/database-url \
  --value "postgresql://contoso:<password>@${NEW_DB_ENDPOINT}:5432/contoso" \
  --type SecureString \
  --overwrite \
  --region us-west-2
```

**Step 6 — Force ECS redeployment in us-west-2** *(picks up new SSM parameter)*
```bash
aws ecs update-service \
  --cluster contoso-prod \
  --service contoso-webapp \
  --force-new-deployment \
  --region us-west-2

# Monitor rollout
aws ecs wait services-stable \
  --cluster contoso-prod \
  --services contoso-webapp \
  --region us-west-2
```

**Step 7 — Smoke test us-west-2**
```bash
# Run the full smoke test suite against the standby
WEBAPP_URL=https://us-west-2-alb-endpoint pytest tests/smoke/ -v

# Must see: all tests PASSED before proceeding
```

**Step 8 — Scale up ECS in us-west-2**
```bash
# Warm standby ran at desired_count=1; scale for production load
aws ecs update-service \
  --cluster contoso-prod \
  --service contoso-webapp \
  --desired-count 2 \
  --region us-west-2
```

**Step 9 — Notify stakeholders**
```
Post in #incidents:
"Failover complete at [timestamp]. Serving from us-west-2.
ALB: [endpoint]. Users may need to re-authenticate (sessions not replicated).
Batch reconciliation: check for in-flight jobs [status].
RCA in progress."
```

---

## Failback Procedure *(when us-east-1 is restored)*

1. **Restore and validate us-east-1** — fix the root cause, verify ALB health check passes
2. **Establish new replication** — set up RDS read replica from us-west-2 (now primary) back to us-east-1
3. **Wait for replica lag to reach 0** — `aws rds describe-db-instances` → `ReplicaLag`
4. **Smoke test us-east-1** before shifting any traffic
5. **Gradual traffic shift** via Route 53 weighted routing:
   - Set us-east-1 weight to 10, monitor for 30 min
   - Move to 50/50, monitor for 30 min
   - Move to 100% us-east-1
6. **Re-promote us-west-2 to replica** — demote from primary, re-establish read replica relationship
7. **Update SSM Parameter in us-east-1** to point to us-east-1 RDS endpoint
8. **Scale down us-west-2 ECS** back to warm standby desired_count=1

---

## Testing Cadence

| Test | Frequency | Method |
|---|---|---|
| Full failover drill | Quarterly | Maintenance window, execute full runbook against staging |
| Read replica lag check | Daily | CloudWatch metric `ReplicaLag` alert if > 60s |
| us-west-2 ECS health | Weekly | Automated health check via CloudWatch synthetic canary |
| S3 CRR lag check | Weekly | CloudWatch metric `ReplicationLatency` |
| Runbook review | Semi-annually | Ensure steps still match current infrastructure |
