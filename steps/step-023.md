# Day 023 — Build the queue port and Celery Redis transport

> Week 4 · Asynchronous telemetry and observability · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Run asynchronous telemetry locally through one replaceable transport.

- Define a transport-neutral publish/consume port and shared handler entry point.
- Implement Celery with Redis broker for local development.
- Configure exactly one active transport and connect the outbox relay.

**Deliverable:** a working Celery/Redis transport behind the queue port

## 2. Why this matters

The brief starts with Celery/Redis and later moves to SQS; a port prevents queue-specific logic from spreading into domain services.

## 3. Previous work done

**Immediate dependencies:** [Day 004](step-004.md), [Day 021](step-021.md), [Day 022](step-022.md).

Before starting, all 22 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 22 earlier steps</summary>

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

</details>

## 4. How to build it

- Use separate telemetry/evaluation/risk task routes if useful, but one Redis broker and one active transport in the environment.
- Enable acks_late, bounded task time limits, controlled prefetch, and reject-on-worker-loss behavior after reviewing current Celery semantics.
- Tune Redis visibility timeout against maximum task duration; document redelivery implications.
- Disable unnecessary result storage or keep only safe metadata; never put content in task arguments/results.
- Validate configuration rejects simultaneous Celery and SQS active transports; Kafka remains an optional isolated lab.

## 5. Workflow for today

1. Define the queue port and message envelope mapping.
2. Add Celery app/worker entry points and locked dependencies.
3. Connect the relay and run one worker locally.
4. Send synthetic events and inspect acknowledgement/redelivery behavior.
5. Test invalid dual-transport configuration and document local operations.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
PYTHONPATH=backend .venv/Scripts/python -m celery -A app.workers.celery_app worker --loglevel=info --pool=solo
.venv/Scripts/python -m pytest backend/tests/integration/test_celery_transport.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] A request produces an outbox event consumed by the local worker.
- [ ] Task payloads/results contain metadata only.
- [ ] Acks-late and visibility settings are documented and tested.
- [ ] Configuration cannot activate two telemetry transports.

## 8. Done and handoff

Complete the deliverable: **a working Celery/Redis transport behind the queue port**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-023: connected the outbox to one active Celery/Redis transport`

Next: [Day 024](step-024.md) — Make background workers idempotent and retry-safe.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
