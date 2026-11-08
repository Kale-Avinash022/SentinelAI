# Day 027 — Test queue failures redelivery and backpressure

> Week 4 · Asynchronous telemetry and observability · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Prove the asynchronous path survives broker and worker failures within explicit capacity limits.

- Add local fault controls for broker outage, worker pause/crash, slow handlers, and poison events.
- Measure outbox growth, queue age, redelivery, and dead-letter behavior.
- Define ingestion backpressure when durable telemetry capacity is exhausted.

**Deliverable:** a measured queue failure and backpressure report

## 2. Why this matters

Zero acknowledged-event loss is possible only with bounded durability and honest admission control; an unlimited in-memory buffer is not reliability.

## 3. Previous work done

**Immediate dependencies:** [Day 022](step-022.md), [Day 023](step-023.md), [Day 024](step-024.md), [Day 025](step-025.md), [Day 026](step-026.md).

Before starting, all 26 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 26 earlier steps</summary>

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

</details>

## 4. How to build it

- Keep fault injection test-only and target-allowlisted; never point controls at real providers or cloud resources.
- Pause workers and confirm durable backlog grows; resume and measure drain without duplicate effects.
- When outbox capacity/age limits are reached, reject new work with a controlled retryable error before acknowledging it; do not silently drop events.
- Tune batch size/prefetch/connection pools using measurements, not arbitrary numbers.
- Record broker-specific caveats so the later SQS adapter can be compared fairly.

## 5. Workflow for today

1. Write a failure matrix with expected behavior and invariants.
2. Run worker crash, broker outage, slow handler, and poison-message scenarios.
3. Inspect outbox/queue/dead-letter metrics after each run.
4. Restore dependencies and verify backlog drains exactly once at the effect layer.
5. Save measured recovery times and unresolved limits.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/integration/test_queue_failures.py -q
.venv/Scripts/python -m pytest backend/tests/integration/test_backpressure.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Acknowledged events survive tested broker/worker interruptions.
- [ ] Backpressure rejects safely before durable capacity is exceeded.
- [ ] Resumed workers drain without double-counting or unsafe side effects.
- [ ] Fault controls cannot target external systems.

## 8. Done and handoff

Complete the deliverable: **a measured queue failure and backpressure report**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-027: tested queue outages, redelivery, dead letters, and bounded backpressure`

Next: [Day 028](step-028.md) — Verify end-to-end telemetry and privacy.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
