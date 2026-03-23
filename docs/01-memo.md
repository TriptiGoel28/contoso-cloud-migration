# Cloud Migration Strategy: Why We Refactor on the Way In

**MEMORANDUM**

**To:** Sarah Chen, Chief Financial Officer
**From:** David Okafor, Chief Technology Officer
**Date:** March 23, 2026
**Re:** Cloud Migration Strategy Decision - Approved Approach

---

## Decision

We will not perform a lift-and-shift migration. We will containerize our three core workloads and progressively replace on-premises dependencies with AWS managed services using the Strangler Fig pattern. This memo documents the rationale, the risks we are accepting, and the risks we are avoiding.

## What We Are Doing and Why

A pure lift-and-shift -- moving our existing VMs directly onto EC2 instances -- is technically the fastest path to "running in the cloud." It is also the most expensive mistake we can make right now.

Our current infrastructure carries three pieces of undocumented coupling that a lift-and-shift would simply relocate without resolving: a hardcoded IP reference to the internal ledger API, a shared NFS mount used as an integration bus between the web application and the batch reconciliation job, and a batch process that writes directly to the reporting database schema, bypassing the application layer entirely. Moving these as-is means paying AWS prices for on-premises architecture. We would then face a second, harder migration in 18 months once we hit scaling limits.

The Strangler Fig pattern changes the sequence. We containerize each workload first, forcing us to externalize configuration, eliminate filesystem coupling, and define explicit service boundaries. As each workload runs cleanly in a container, we replace the backing resource: the shared NFS mount becomes S3 object storage, the hardcoded IP reference is replaced with a proper service endpoint or AWS PrivateLink connection, and the batch job is re-architected as an event-driven worker triggered by S3 file arrival rather than a 2am cron job hoping the mount is available.

The result is that by the time we cut over to AWS, we are deploying cloud-native workloads onto ECS Fargate, RDS, ElastiCache, and EventBridge -- not VMs pretending to be cloud.

## Risks We Are Accepting

**Longer timeline.** This approach adds approximately six weeks to the migration compared to a straight lift-and-shift. We are accepting this cost.

**Team skill gap.** Our operations team has no prior experience with ECS task definitions, CloudWatch log routing, or IAM role-based access for containers. We are funding two weeks of targeted AWS training before the containerization phase begins.

**Two environments to maintain during transition.** For approximately eight weeks, we will run on-premises infrastructure in parallel with the AWS environment. This creates operational overhead and a non-trivial risk of configuration drift. We will mitigate this with weekly reconciliation checks and a hard decommission date.

## Risks We Are Avoiding

**Cloud-native debt.** A lift-and-shift trades immediate simplicity for compounding complexity. Every month we run 10.0.1.45 as a hardcoded address inside a cloud environment is a month we cannot use auto-scaling, blue-green deployments, or multi-region failover. We are avoiding this debt entirely.

**Paying reserved-instance prices for under-sized VMs.** Our web server currently runs on hardware provisioned for peak load in 2019. If we lift-and-shift to an equivalent EC2 instance type, we will commit reserved-instance pricing to a workload we have not profiled in three years. Containerizing first allows us to right-size against actual Fargate task metrics before making any reserved-capacity commitments.

## Call to Action

I am requesting CFO sign-off on the eight-week parallel-run budget, estimated at $1,200 in additional cloud spend above our current AWS Development account costs. Detailed cost projections are in the attached cost model (Challenge 8). Engineering kickoff is scheduled for March 30. Without budget confirmation by March 27, we push kickoff by one sprint.

---

*David Okafor, CTO -- Contoso Financial*
