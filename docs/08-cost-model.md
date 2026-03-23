# Challenge 8 — The Bill: Cost Model

**On-Premises vs AWS — 12-Month Projection**

*Estimates based on AWS public pricing (us-east-1), March 2026. Use the [AWS Pricing Calculator](https://calculator.aws) for binding estimates.*

---

## Current On-Premises Costs

| Resource | Monthly | Notes |
|---|---|---|
| web-01 (8 vCPU / 32GB) | $2,000 | Amortized CapEx + power + rack |
| batch-01 (4 vCPU / 16GB) | $2,000 | Vastly over-provisioned for a cron job |
| db-01 (16 vCPU / 64GB + 2TB SSD) | $2,000 | Includes storage amortization |
| IT ops labor (patching, on-call) | $2,500 | 0.5 FTE, server-level maintenance |
| NAS storage (10TB) | $200 | nas-01 shared filesystem |
| Network / colocation | $800 | Rack space, transit, cross-connects |
| **Total** | **$9,500** | **$114,000/yr** |

**Hidden costs not captured above:**
- Python 2.7 EOL security debt (unquantified breach risk)
- Postgres 11 EOL (November 2023) — unpatched CVEs
- 3 servers × ~40h/yr manual patching = ~120h engineer time/yr

---

## AWS Year 1 — Option B (ECS Fargate + RDS + ElastiCache)

### On-Demand Pricing

| Service | Specification | Monthly | Notes |
|---|---|---|---|
| ECS Fargate — webapp | 2 tasks × 0.5 vCPU / 1GB, 24/7 | $58 | $0.04048/vCPU-hr + $0.004445/GB-hr |
| ECS Fargate — batch | 1 task × 0.25 vCPU / 0.5GB, 24/7 | $14 | Always-on event listener; ~4h/day active |
| RDS Postgres 15 | db.t3.medium, Multi-AZ, 20GB gp3 | $120 | Multi-AZ doubles the single-AZ cost of ~$60 |
| ElastiCache Redis 7 | cache.t3.micro, 1 node | $25 | Single node — not HA; upgrade if sessions are critical |
| Application Load Balancer | ~1,000 req/hr estimated | $20 | Fixed + LCU charges |
| S3 | ~50GB storage, low request volume | $3 | Reconciliation files only |
| CloudWatch Logs + metrics | webapp + batch log ingestion | $25 | ~10GB/mo estimated |
| NAT Gateway | 1 NAT × $0.045/hr | $33 | Private subnets egress to S3/SSM |
| Data transfer out | ~100GB/mo | $9 | First 1GB free, $0.09/GB thereafter |
| ECR | 2 repos, ~2GB storage | $1 | $0.10/GB-month |
| Route 53 | 1 hosted zone + 2 health checks | $5 | |
| SSM Parameter Store | Standard parameters (free tier) | $0 | |
| **Total on-demand** | | **$313/mo** | **$3,756/yr** |

### With 1-Year Reserved Instances

| Service | Saving | Reserved Monthly |
|---|---|---|
| RDS db.t3.medium (partial upfront, 1yr) | ~33% | $80 |
| ElastiCache cache.t3.micro (1yr) | ~36% | $16 |
| Fargate Savings Plan (compute, 1yr) | ~20% | ~$58 → $47 |
| **Total with reservations** | | **~$263/mo = $3,156/yr** |

---

## AWS Year 2 — Optimization Path

After 6 months of CloudWatch metrics, right-size and optimize:

| Optimization | Action | Saving |
|---|---|---|
| Fargate Spot for batch | Switch batch ECS service to FARGATE_SPOT capacity provider | ~70% on batch compute → $14 → $4 |
| Right-size RDS | If CPU avg <20% over 6mo, move to db.t3.small | ~$40/mo |
| S3 Intelligent-Tiering | Auto-tier reconciliation archives after 30 days | ~$1/mo |
| NAT Gateway VPC Endpoints | Add S3 + SSM VPC endpoints to avoid NAT charges for AWS API calls | ~$10/mo |

**Projected Year 2: ~$215/mo = $2,580/yr**

---

## Comparison Summary

| | On-Prem | AWS Y1 On-Demand | AWS Y1 Reserved | AWS Y2 Optimized |
|---|---|---|---|---|
| Monthly | $9,500 | $313 | $263 | $215 |
| Annual | $114,000 | $3,756 | $3,156 | $2,580 |
| vs On-Prem | baseline | **-97%** | **-97%** | **-98%** |
| CapEx | $15,000/yr amortized | $0 | ~$500 upfront | $0 ongoing |

---

## What You're Not Paying For Anymore

- Server hardware refresh cycles
- Colocation rack space and power
- Manual OS patching (Fargate is serverless compute; RDS patches are managed)
- NAS storage infrastructure
- The Python 2.7 and Postgres 11 security debt you've been deferring

---

## Assumptions

1. Traffic: ~1,000 requests/hour to webapp (low-medium volume)
2. Data transfer: ~100GB/month outbound (conservative estimate)
3. Log volume: ~10GB/month across both services
4. Reconciliation files: ~50GB total storage in S3, growing slowly
5. RDS storage: 20GB initial, auto-scaling to 100GB max
6. No egress to on-prem after cutover (no VPN/Direct Connect costs)
7. No CloudFront CDN (add ~$10-20/mo if needed for static assets)
8. No WAF (add ~$30/mo + $0.60/1M requests if compliance requires it)
9. Pricing as of March 2026, us-east-1 region
10. Does not include one-time migration costs (engineer time, tooling)

> **Note:** The on-prem cost is likely understated. It excludes: engineer time for hardware failures, unplanned downtime costs, the implicit cost of running EOL software (Python 2.7, Postgres 11), and the opportunity cost of capacity that can't be elastically reduced during low-demand periods. The real TCO comparison favors cloud more strongly than these numbers suggest.
