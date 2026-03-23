# Challenge 10 — The Undo: Rollback Plan

> *"This document exists because something will go wrong mid-cutover.
> The question is not whether we need it — but whether we wrote it
> before or after 4am."*

---

## How to Use This Document

Each workload has rollback procedures at four stages. Find your stage, execute the numbered steps, verify, notify.

**Rollback stages:**
- **Pre-cutover** — before any traffic has shifted to AWS
- **During cutover** — Route 53 weighted routing is live (traffic split in progress)
- **Post-cutover < 24h** — all traffic on AWS, within the first 24 hours
- **Post-cutover > 72h** — rollback window is closing

---

## Workload 1: Web Application

### Pre-cutover
No rollback needed. Containers are built and tested locally. On-prem `web-01` is unchanged and serving 100% of traffic.

### During Cutover (Route 53 weighted split)

**Trigger:** Error rate > 1% on ECS tasks, OR p99 latency > 2s, OR `test_webapp_health` smoke test fails.

| Step | Action |
|---|---|
| 1 | In Route 53, set the ECS ALB record weight to **0** |
| 2 | Verify DNS propagates: `dig api.contoso.internal` should resolve to on-prem IP |
| 3 | Confirm CloudWatch shows 0 requests reaching ECS ALB |
| 4 | Keep ECS services running (desired_count unchanged) for rapid re-enable |

**Time:** 2 minutes
**Who:** On-call engineer
**Verification:** `curl http://api.contoso.internal/health` — should be served by on-prem

### Post-Cutover < 24h

Same as During Cutover — Route 53 weight to 0. ECS services remain warm.

**Additional step:** Check ECS task logs in CloudWatch for root cause before re-attempting cutover.

### Post-Cutover > 72h

**Rollback window closes when:** `web-01` is powered down or decommissioned.

If rollback is needed after this point:
1. Restore `web-01` from backup (estimated: 4 hours including validation)
2. Re-point Route 53 to on-prem IP
3. Document what failed — this is now a forward-fix-only situation for future incidents

---

## Workload 2: Batch Reconciliation *(hardest rollback scenario)*

### Pre-cutover
Old cron job (`0 2 * * *` on `batch-01`) is still active. New event-driven worker has only been tested in staging. No rollback needed.

### During Cutover ⚠️

**This is the most dangerous rollback scenario.** If both the old cron and the new event-driven worker processed the same files, you have duplicate reconciliation records.

**Trigger:** Reconciliation count doesn't match expected, Finance reports discrepancies, or duplicate records detected.

| Step | Action |
|---|---|
| 1 | **Stop the ECS batch worker immediately:** `aws ecs update-service --cluster contoso-prod --service contoso-batch --desired-count 0 --region us-east-1` |
| 2 | **Check for duplicates:** |

```sql
-- Run this to identify affected transaction IDs
SELECT transaction_id, COUNT(*) AS cnt, array_agg(batch_run_id) AS runs
FROM reporting.reconciled_transactions
GROUP BY transaction_id
HAVING COUNT(*) > 1
ORDER BY cnt DESC;
```

| Step | Action |
|---|---|
| 3 | **If duplicates found, deduplicate** (keep the oldest record per transaction): |

```sql
-- IMPORTANT: Run in a transaction, verify counts before COMMIT
BEGIN;

DELETE FROM reporting.reconciled_transactions
WHERE id NOT IN (
  SELECT MIN(id)
  FROM reporting.reconciled_transactions
  GROUP BY transaction_id
);

-- Verify: should return 0 rows
SELECT transaction_id, COUNT(*)
FROM reporting.reconciled_transactions
GROUP BY transaction_id
HAVING COUNT(*) > 1;

-- If clean, commit. If not, ROLLBACK and investigate.
COMMIT;
```

| Step | Action |
|---|---|
| 4 | **Re-enable on-prem cron:** `ssh batch-01.contoso.local` → `crontab -e` → uncomment `0 2 * * *` line |
| 5 | **Move any S3-processed files back** to on-prem NFS for re-processing: copy from `s3://reconciliation-input/processed/` back to `/mnt/reports/shared/incoming/` |
| 6 | **Verify next cron run** produces correct counts |

**Time:** 20-30 minutes
**Who:** Data engineer + DBA (both required)
**Verification:** `SELECT COUNT(*) FROM reporting.reconciled_transactions` matches pre-cutover baseline

### Post-Cutover < 24h

Same procedure as During Cutover. The deduplication window is still open.

### Post-Cutover > 72h

After 72h of successful batch runs without the old cron:
- On-prem cron has been disabled and `batch-01` may be decommissioned
- Rollback requires: restore `batch-01` from backup, restore NFS mount, re-run deduplication on 3+ days of potential duplicates
- **This is a multi-hour, high-risk operation.** Escalate to engineering leadership before attempting.

---

## Workload 3: Reporting Database *(longest rollback)*

### Pre-cutover
RDS is a read replica of on-prem. On-prem `db-01` is authoritative. No rollback needed.

### During Cutover

**Trigger:** BI tools can't connect to RDS, query results differ between on-prem and RDS, or the `test_cross_schema_view_accessible` data integrity test fails.

| Step | Action |
|---|---|
| 1 | Identify which teams are affected (check Metabase error logs) |
| 2 | **Update Metabase connections** for each affected team (see contact list below) |
| 3 | For each team: change Metabase database host from `<rds-endpoint>` back to `db-01.contoso.local:5432` |
| 4 | Run the cross-schema view query on on-prem to verify data is intact |
| 5 | Notify each team lead that they're back on on-prem |

**Team contact list:**
| Team | Contact | Metabase Instance |
|---|---|---|
| Analytics | analytics-team@contoso.com | metabase-analytics.contoso.internal |
| Finance | bi-finance@contoso.com | metabase-finance.contoso.internal |
| Ops | ops-reporting@contoso.com | Shared metabase.contoso.internal |
| Compliance | compliance-data@contoso.com | Shared metabase.contoso.internal |
| Risk | risk-team@contoso.com | Direct psql access |

**Time:** 15 minutes per team × 5 teams = up to 75 minutes
**Who:** DBA + Project Manager (to coordinate team notifications)
**Verification:** Run `SELECT COUNT(*) FROM reporting.customer_reconciliation_summary` on on-prem. Each team confirms Metabase dashboards load.

### Post-Cutover < 24h

Same as during cutover. RDS may have additional writes that aren't in on-prem. If so:
```bash
# Dump incremental changes from RDS since cutover
pg_dump -h <rds-endpoint> -U contoso -d contoso \
  --schema=app --schema=reporting \
  --data-only > rds_incremental_$(date +%Y%m%d_%H%M%S).sql

# Restore to on-prem (review carefully first)
psql -h db-01.contoso.local -U postgres contoso < rds_incremental_*.sql
```

### Post-Cutover > 72h

**Rollback window closes when:** All 5 BI teams have confirmed stable Metabase connectivity to RDS for > 72 consecutive hours AND on-prem `db-01` has been powered down.

If rollback is required after this point:
1. `pg_dump` full database from RDS (~2 hours for a multi-GB database)
2. Restore to emergency on-prem instance (~3 hours)
3. Update all 5 teams' connections + test
4. Total estimated time: **6-8 hours**

> This is the "4am scenario" referenced in the architecture review. After the rollback window closes, we are in forward-fix mode — restore from RDS snapshot, bring up a new on-prem instance, update connections. This is not a failure condition; it means the migration succeeded and we are now in standard incident response.

---

## Rollback Window Closure Criteria

The rollback window is formally closed when **ALL** of the following conditions are met:

- [ ] Reporting DB on RDS, all 5 BI teams confirmed connectivity for > 72 consecutive hours
- [ ] Web app on ECS Fargate, 100% traffic, error rate < 0.1% for > 72 hours
- [ ] Batch reconciliation completed ≥ 3 successful event-driven runs, results verified by Finance
- [ ] All data integrity tests passing against RDS: `pytest tests/data_integrity/ -v`
- [ ] On-prem hardware powered down (NOT decommissioned — keep powered-off for 30 days cold standby)

**After this point: forward-fix only.**

The on-prem hardware remains available (powered off) for 30 days as cold standby insurance. After 30 days, formal decommission can proceed.

---

## Appendix: Quick Reference

| Situation | First Action |
|---|---|
| Web app returning 5xx | Route 53 weight → 0 on ECS record |
| Batch job duplicates | Stop ECS service → deduplicate query → re-enable on-prem cron |
| BI team can't connect | Re-point Metabase to db-01.contoso.local |
| RDS unreachable | Check RDS status → failover to replica (see DR plan) |
| Everything is on fire | Call incident commander, declare major incident, start DR plan |
