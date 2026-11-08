# Day 090 — Optional Kubernetes operations and platform decision

> Optional Week 13 · Optional advanced extensions  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Exercise rollout, scaling, isolation, and recovery locally, then compare Kubernetes with ECS honestly.

- Test rolling updates, readiness, graceful worker shutdown, HPA, and disruption budgets.
- Add NetworkPolicy only with a CNI that enforces it and verify allowed/blocked flows.
- Connect existing OpenTelemetry/Prometheus monitoring and write a platform-choice ADR.

**Deliverable:** a tested local Kubernetes operations lab and platform-choice ADR

## 2. Why this matters

Operating Kubernetes involves more than writing YAML; rollout safety, policy enforcement, scaling signals, and stateful operations must be demonstrated.

## 3. Previous work done

**Immediate dependencies:** [Day 025](step-025.md), [Day 026](step-026.md), [Day 058](step-058.md), [Day 065](step-065.md), [Day 069](step-069.md), [Day 074](step-074.md), [Day 077](step-077.md), [Day 084](step-084.md), [Day 089](step-089.md).

Core Days 001–084 must be complete. Earlier optional labs are listed for context; complete the pair you selected and the immediate dependencies above. Unselected optional pairs may remain deferred. This is an expected-state checklist, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 89 earlier steps</summary>

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
- [ ] [Day 075](step-075.md) — deployed and smoke-tested the gated AWS lab, or recorded a local-only deferral
- [ ] [Day 076](step-076.md) — drilled RDS restore, SQS redrive, worker restart, and queue cutover
- [ ] [Day 077](step-077.md) — tore down the approved AWS lab and checked hidden cost sources
- [ ] [Day 078](step-078.md) — completed architecture diagrams, ADRs, limitations, and operator runbooks
- [ ] [Day 079](step-079.md) — verified clean-clone setup, API examples, OpenAPI, and browser quickstart
- [ ] [Day 080](step-080.md) — mapped brief requirements to code, tests, reports, and honest limitations
- [ ] [Day 081](step-081.md) — built and rehearsed a seven-incident synthetic product demo
- [ ] [Day 082](step-082.md) — prepared interview answers, measured resume claims, and engineering changelog
- [ ] [Day 083](step-083.md) — polished repository docs, license, security posture, and release candidate
- [ ] [Day 084](step-084.md) — accepted or honestly deferred the MVP release and recorded the advanced backlog
- [ ] [Day 085](step-085.md) — added an isolated Kafka KRaft transport with shared-handler parity
- [ ] [Day 086](step-086.md) — benchmarked Kafka semantics and recorded an adoption decision
- [ ] [Day 087](step-087.md) — built a typed LangGraph planner with no production mutation tools
- [ ] [Day 088](step-088.md) — integrated graph proposals with external approvals and bypass resistance
- [ ] [Day 089](step-089.md) — deployed API, worker, UI, and lab dependencies to local Kubernetes

</details>

## 4. How to build it

- Install/use a reviewed local metrics server for CPU HPA; consider queue-lag scaling only after measuring the existing worker bottleneck.
- Test a bad image/readiness failure and rollback without losing acknowledged events.
- Verify NetworkPolicy enforcement with test pods; if the chosen local CNI does not enforce it, document that limitation rather than claiming isolation.
- Reuse content-free telemetry and dashboards; add no prompt attributes.
- Compare local K8s operational tasks and limitations with the AWS ECS design; do not claim local single-replica services are HA or cheaper without evidence.

## 5. Workflow for today

1. Run a controlled rolling update and failed-readiness rollback.
2. Apply fake-only load and observe HPA/worker shutdown behavior.
3. Test network allow/deny flows and dependency restart recovery.
4. Review monitoring and operational burden versus ECS.
5. Write the final ADR, clean up only approved lab resources, and record optional-track completion.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
kubectl config current-context
kubectl rollout status deployment/sentinel-api
kubectl get hpa,pdb,networkpolicy
kubectl top pods
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Rolling update/rollback preserves readiness and event invariants.
- [ ] HPA uses real local metrics and graceful shutdown is tested.
- [ ] NetworkPolicy claims match actual CNI enforcement tests.
- [ ] The final ADR compares measured behavior and operational cost without hype.

## 8. Done and handoff

Complete the deliverable: **a tested local Kubernetes operations lab and platform-choice ADR**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-090: tested Kubernetes rollout, scaling, network policy, and ECS tradeoffs`

This completes the optional advanced track. Return to the roadmap and record which extensions were actually implemented and tested.

## Safety note

Keep all operations on the verified local cluster. Deleting the cluster/PVCs is destructive and requires explicit approval after evidence is preserved.
