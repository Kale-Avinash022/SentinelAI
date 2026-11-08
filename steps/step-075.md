# Day 075 — Deploy a short-lived AWS test environment

> Week 11 · Cost-conscious AWS deployment · Core roadmap  
> Plan for about 3–4 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Deploy the reviewed synthetic-data lab, verify it, and keep the spending window bounded.

- Apply only the approved Terraform plan and deploy gateway, worker, and dashboard services.
- Run migrations as a controlled one-off task before serving traffic.
- Configure CloudWatch alarms, HTTPS where a validated domain/certificate exists, and smoke tests.

**Deliverable:** a verified short-lived AWS deployment or an explicitly approved local-only deferral

## 2. Why this matters

A real deployment demonstrates operations only when identity, migrations, health, monitoring, and teardown are controlled.

## 3. Previous work done

**Immediate dependencies:** [Day 061](step-061.md), [Day 062](step-062.md), [Day 071](step-071.md), [Day 072](step-072.md), [Day 073](step-073.md), [Day 074](step-074.md).

Before starting, all 74 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 74 earlier steps</summary>

- [ ] [Day 001](step-001.md) — defined MVP scope, six modules, safety categories, and architecture decisions
- [ ] [Day 002](step-002.md) — initialized Git, minimal structure, .venv, locked dependencies, and smoke test
- [ ] [Day 003](step-003.md) — added repeatable lint, type, test, dependency, and secret checks
- [ ] [Day 004](step-004.md) — started local PostgreSQL/Redis with health checks and least-privilege roles
- [ ] [Day 005](step-005.md) — connected async SQLAlchemy and verified migration upgrade/downgrade behavior
- [ ] [Day 006](step-006.md) — built FastAPI app factory, versioned API, health checks, and safe errors
- [ ] [Day 007](step-007.md) — verified clean-setup bootstrap and closed foundation gaps
- [ ] [Day 008](step-008.md) — defined tenant models, provider catalog, and four-role permission matrix
- [ ] [Day 009](step-009.md) — applied constrained tenant schema, indexes, and least-privilege grants
- [ ] [Day 010](step-010.md) — enforced forced RLS with transaction-local tenant context
- [ ] [Day 011](step-011.md) — proved tenant isolation across APIs, writes, and pooled sessions
- [ ] [Day 012](step-012.md) — added secure login, short JWT sessions, refresh rotation, and RBAC
- [ ] [Day 013](step-013.md) — implemented application/key lifecycle, hashed credentials, and append-only audit
- [ ] [Day 014](step-014.md) — added immutable config/prompt versions, redacted diffs, and CAS activation
- [ ] [Day 015](step-015.md) — defined chat contracts and deterministic multi-failure fake providers
- [ ] [Day 016](step-016.md) — integrated LiteLLM behind the port with local and gated real adapters
- [ ] [Day 017](step-017.md) — added durable provider attempts and scoped idempotency claims
- [ ] [Day 018](step-018.md) — handled unknown paid outcomes without unsafe automatic replay
- [ ] [Day 019](step-019.md) — built snapshot-based routing with capability, privacy, and budget checks
- [ ] [Day 020](step-020.md) — added SSE streaming, cancellation accounting, and tenant rate limits
- [ ] [Day 021](step-021.md) — verified authenticated gateway contracts, budgets, streaming, and idempotency
- [ ] [Day 022](step-022.md) — added metadata-only events and transactional outbox persistence
- [ ] [Day 023](step-023.md) — connected the outbox to one active Celery/Redis transport
- [ ] [Day 024](step-024.md) — made worker effects idempotent with bounded retries and dead letters
- [ ] [Day 025](step-025.md) — added correlated OpenTelemetry traces across API, queue, and workers
- [ ] [Day 026](step-026.md) — exposed bounded metrics and provisioned Prometheus/Grafana dashboards
- [ ] [Day 027](step-027.md) — tested queue outages, redelivery, dead letters, and bounded backpressure
- [ ] [Day 028](step-028.md) — verified end-to-end event accounting and metadata-only telemetry
- [ ] [Day 029](step-029.md) — built idempotent latency/error/token window aggregates
- [ ] [Day 030](step-030.md) — added versioned pricing, estimated costs, and budget reconciliation
- [ ] [Day 031](step-031.md) — implemented request/overview/latency/cost APIs with cursor pagination
- [ ] [Day 032](step-032.md) — scaffolded React dashboard with secure sessions and typed API access
- [ ] [Day 033](step-033.md) — visualized latency, errors, tokens, traffic, and estimated cost
- [ ] [Day 034](step-034.md) — added metadata-only request history, filters, and trace drill-down
- [ ] [Day 035](step-035.md) — verified monitoring MVP and created the first 40 quality cases
- [ ] [Day 036](step-036.md) — built versioned detector framework with warm-up and hysteresis
- [ ] [Day 037](step-037.md) — detected outages, cost/token spikes, and abnormal API usage
- [ ] [Day 038](step-038.md) — added config/prompt regression comparison and activation evidence
- [ ] [Day 039](step-039.md) — implemented transparent weighted risk scoring with provisional coverage
- [ ] [Day 040](step-040.md) — created deduplicated incidents with evidence, transitions, and audit
- [ ] [Day 041](step-041.md) — added incident APIs and dashboard investigation/acknowledgment
- [ ] [Day 042](step-042.md) — verified detection pipeline and expanded the dataset to 100 cases
- [ ] [Day 043](step-043.md) — defined quality rubric/evaluation contracts and reached 150 cases
- [ ] [Day 044](step-044.md) — completed 200 quality cases and locked a blind evaluation split
- [ ] [Day 045](step-045.md) — added bounded in-memory quality features without content storage
- [ ] [Day 046](step-046.md) — calibrated evaluators and measured precision, recall, FPR, and agreement
- [ ] [Day 047](step-047.md) — connected calibrated quality evidence to risk scores and incidents
- [ ] [Day 048](step-048.md) — added async regression evaluation and optional Ragas/DeepEval adapter
- [ ] [Day 049](step-049.md) — verified reproducible quality evaluation, privacy, and incident integration
- [ ] [Day 050](step-050.md) — encoded A/B/C recovery policy, action contracts, and state machine
- [ ] [Day 051](step-051.md) — implemented digest-bound expiring human approvals and rejection
- [ ] [Day 052](step-052.md) — built leased, fenced, idempotent recovery execution and reconciliation
- [ ] [Day 053](step-053.md) — added shared circuit breakers, bulkheads, and approved healthy fallback
- [ ] [Day 054](step-054.md) — added bounded throttling, safe retry classification, and retry cost caps
- [ ] [Day 055](step-055.md) — implemented approved last-known-good rollback and recovery verification
- [ ] [Day 056](step-056.md) — verified recovery policies, approvals, fencing, rollback, and escalation
- [ ] [Day 057](step-057.md) — verified timeout, 429, 5xx, malformed, empty, slow, and reset behavior
- [ ] [Day 058](step-058.md) — tested Redis, DB, queue, dashboard, evaluator, and recovery failures
- [ ] [Day 059](step-059.md) — proved untrusted model text cannot authorize or execute recovery
- [ ] [Day 060](step-060.md) — hardened tenant isolation, sessions, key rotation, and exposed-key handling
- [ ] [Day 061](step-061.md) — implemented privacy modes, encrypted diagnostics, access audit, and retention
- [ ] [Day 062](step-062.md) — ran Bandit, Semgrep, pip-audit, Trivy checks, secret scan, and local ZAP
- [ ] [Day 063](step-063.md) — published measured security/failure reports and verified audit/restore behavior
- [ ] [Day 064](step-064.md) — benchmarked 20-user normal fake-provider traffic with real percentiles
- [ ] [Day 065](step-065.md) — measured traffic spikes, worker backlog, saturation, and backpressure
- [ ] [Day 066](step-066.md) — measured detection-to-recovery time, approval delay, and backlog recovery
- [ ] [Day 067](step-067.md) — tuned indexed queries and made an evidence-based partitioning decision
- [ ] [Day 068](step-068.md) — reported request, retry, evaluation, failure, and infrastructure costs
- [ ] [Day 069](step-069.md) — evaluated precise SLOs, coverage targets, and performance regressions
- [ ] [Day 070](step-070.md) — finalized measured benchmark, cost, recovery, and SLO evidence
- [ ] [Day 071](step-071.md) — prepared AWS budget alerts, OIDC identity, pricing checks, and approval gates
- [ ] [Day 072](step-072.md) — defined plan-only SQS/DLQ, ECR, S3, secrets, IAM, and logging resources
- [ ] [Day 073](step-073.md) — implemented SQS visibility-safe consumption, DLQ, dedupe, and cutover
- [ ] [Day 074](step-074.md) — planned ECS/RDS/ALB/cache topology and built scanned deployment images

</details>

## 4. How to build it

- Reconfirm account/region, plan digest, spend cap, start time, and teardown time immediately before apply.
- Run Alembic using the migration identity once; application tasks use the restricted runtime identity.
- Deploy immutable image digests to separate ECS services/task definitions; use ALB routing and health checks.
- Use ACM/Route53 only with an existing approved domain; otherwise keep the lab synthetic and clearly label any HTTP limitation rather than claiming production TLS.
- Run fake-provider smoke tests, queue/DB/secret checks, dashboard browser checks, and alarm tests; record deployment evidence.

## 5. Workflow for today

1. Present the final plan and cost window and obtain explicit approval.
2. Apply the saved approved plan and verify created resources.
3. Run the migration task and deploy API/worker/UI digests.
4. Execute synthetic API, queue, dashboard, and monitoring smoke tests.
5. Record endpoints/evidence without secrets and schedule the teardown checkpoint.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
terraform -chdir=infrastructure/terraform plan -out=tfplan
# Run the next command only after explicit approval of this exact plan and spend window.
terraform -chdir=infrastructure/terraform apply tfplan
aws ecs describe-services --cluster "$SENTINEL_CLUSTER" --services "$SENTINEL_API_SERVICE" "$SENTINEL_WORKER_SERVICE"
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Apply matches the reviewed plan and uses OIDC/least privilege.
- [ ] Migrations complete once before traffic and runtime roles cannot alter schema.
- [ ] Gateway, worker, dashboard, SQS, secrets, and alarms pass synthetic smoke checks.
- [ ] TLS status is accurate and the teardown time is recorded.

## 8. Done and handoff

Complete the deliverable: **a verified short-lived AWS deployment or an explicitly approved local-only deferral**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-075: deployed and smoke-tested the gated AWS lab, or recorded a local-only deferral`

Next: [Day 076](step-076.md) — Drill AWS recovery and queue cutover.

## Safety note

This step requires fresh explicit approval before apply and public exposure. Use synthetic data only. If approval/budget is absent, do not apply; mark cloud deployment not executed.
