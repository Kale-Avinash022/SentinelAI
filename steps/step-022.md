# Day 022 — Design telemetry events and the transactional outbox

> Week 4 · Asynchronous telemetry and observability · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Capture request telemetry durably without making queue availability part of every provider call.

- Define versioned metadata-only telemetry envelopes with event ID, tenant, request, trace, config, and occurred_at.
- Add an outbox table written in the same transaction as request/attempt finalization.
- Create a relay interface with bounded claims and a fake publisher for testing.

**Deliverable:** a versioned telemetry schema and crash-tested transactional outbox

## 2. Why this matters

Writing the database and publishing to a broker are separate effects; an outbox closes the crash gap between them.

## 3. Previous work done

**Immediate dependencies:** [Day 010](step-010.md), [Day 013](step-013.md), [Day 017](step-017.md), [Day 020](step-020.md), [Day 021](step-021.md).

Before starting, all 21 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 21 earlier steps</summary>

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

</details>

## 4. How to build it

- Include schema_version, event_type, stable event_id, organization/application IDs, and redacted payload; exclude prompts, completions, credentials, and arbitrary metadata.
- Write request state and outbox row atomically; never publish inside the transaction before commit.
- Claim rows with SKIP LOCKED or equivalent leasing, track attempts/next_attempt_at, and mark sent only after transport acknowledgement.
- Bound pending rows/age and expose backlog metrics; define backpressure before acknowledging work that cannot be durably recorded.
- Keep the relay publisher injectable so Celery and SQS can share it later.

## 5. Workflow for today

1. Write the event schema and privacy ADR.
2. Add outbox migration, repository, and transactional finalization.
3. Implement relay claiming with a fake publisher.
4. Inject crashes before publish, after publish before mark, and before commit.
5. Verify duplicates are possible but acknowledged events are not lost.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
PYTHONPATH=backend .venv/Scripts/python -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python -m pytest backend/tests/integration/test_outbox.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Request finalization and outbox insertion commit atomically.
- [ ] Crash windows produce at-least-once delivery, not silent loss.
- [ ] Events contain no content bodies or secrets.
- [ ] Pending outbox capacity/age is bounded and observable.

## 8. Done and handoff

Complete the deliverable: **a versioned telemetry schema and crash-tested transactional outbox**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-022: added metadata-only events and transactional outbox persistence`

Next: [Day 023](step-023.md) — Build the queue port and Celery Redis transport.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
