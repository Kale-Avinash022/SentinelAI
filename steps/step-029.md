# Day 029 — Build latency error and token rollups

> Week 5 · Metrics, cost, and monitoring dashboard · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Turn idempotent telemetry into efficient dashboard and detector inputs.

- Create fixed time-window aggregates per organization/application/provider/model/config.
- Track counts, errors, tokens, usage certainty, and mergeable latency buckets.
- Handle late and duplicate events without double-counting.

**Deliverable:** correct tenant-scoped rollups for latency, errors, and tokens

## 2. Why this matters

Scanning raw requests for every dashboard refresh is expensive and makes anomaly windows inconsistent; rollups provide stable, indexed inputs.

## 3. Previous work done

**Immediate dependencies:** [Day 010](step-010.md), [Day 024](step-024.md), [Day 028](step-028.md).

Before starting, all 28 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 28 earlier steps</summary>

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

</details>

## 4. How to build it

- Use explicit window start/end and UTC boundaries; store histogram bucket counts so p95 can be derived without averaging percentiles.
- Keep exact, estimated, and unknown token totals/counts separate.
- Update aggregates inside the same transaction as the inbox dedupe marker.
- Define allowed lateness and correction behavior; never let a late event create a second counted request.
- Add indexes matching tenant/application/time queries and test window boundaries.

## 5. Workflow for today

1. Design aggregate schema and bucket boundaries from expected latency ranges.
2. Add migration and projection handler.
3. Replay a deterministic event set with duplicates and late arrivals.
4. Compare rollups against a simple independent reference calculation.
5. Document accuracy limits introduced by bucketing.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
PYTHONPATH=backend .venv/Scripts/python -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python -m pytest backend/tests/unit/test_rollups.py backend/tests/integration/test_rollups.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Duplicate events do not change totals twice.
- [ ] Late events follow one documented correction policy.
- [ ] Percentiles come from mergeable distributions, not averaged p95 values.
- [ ] Unknown usage is not silently converted to zero.

## 8. Done and handoff

Complete the deliverable: **correct tenant-scoped rollups for latency, errors, and tokens**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-029: built idempotent latency/error/token window aggregates`

Next: [Day 030](step-030.md) — Calculate costs with versioned pricing.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
