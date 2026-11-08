# Day 024 — Make background workers idempotent and retry-safe

> Week 4 · Asynchronous telemetry and observability · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Ensure redelivered events cannot double-count metrics or repeat side effects.

- Add processed_events/inbox deduplication keyed by consumer and event ID.
- Commit dedupe markers and projected effects in one transaction.
- Add bounded worker retries and a local dead-letter path with redacted diagnostics.

**Deliverable:** idempotent shared handlers with bounded retry and dead-letter handling

## 2. Why this matters

Celery/Redis and SQS Standard can deliver more than once; correctness must live in the consumer, not assumptions about the broker.

## 3. Previous work done

**Immediate dependencies:** [Day 010](step-010.md), [Day 022](step-022.md), [Day 023](step-023.md).

Before starting, all 23 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 23 earlier steps</summary>

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

</details>

## 4. How to build it

- Use a unique constraint and transaction-scoped insert so concurrent consumers cannot both apply an effect.
- Classify transient infrastructure errors separately from poison schemas/permanent validation failures.
- Retry with exponential backoff and jitter under a maximum count/deadline; do not retry invalid events forever.
- Move repeatedly failing events to a dead-letter table/queue with event ID, error class, attempt count, and trace—not raw content.
- Define authorized replay that still passes through dedupe and schema validation.

## 5. Workflow for today

1. Add inbox migration and handler transaction.
2. Implement retry classification and dead-letter handling.
3. Deliver the same event concurrently and sequentially to one consumer.
4. Crash after effect commit but before acknowledgement.
5. Verify replay and dead-letter inspection remain tenant-scoped.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/integration/test_worker_idempotency.py backend/tests/integration/test_dead_letter.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Duplicate/concurrent delivery applies each effect once.
- [ ] A crash before acknowledgement causes safe redelivery.
- [ ] Poison events reach the dead-letter path within bounded attempts.
- [ ] Replay cannot bypass tenant authorization or deduplication.

## 8. Done and handoff

Complete the deliverable: **idempotent shared handlers with bounded retry and dead-letter handling**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-024: made worker effects idempotent with bounded retries and dead letters`

Next: [Day 025](step-025.md) — Add OpenTelemetry tracing across API and workers.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
