# Day 025 — Add OpenTelemetry tracing across API and workers

> Week 4 · Asynchronous telemetry and observability · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Correlate gateway, outbox, transport, worker, and database work without recording prompt content.

- Instrument FastAPI, provider calls, SQL, relay, and worker handlers.
- Propagate trace context through event envelopes and transport metadata.
- Add a local OTLP collector/trace backend profile and exporter tests.

**Deliverable:** content-free distributed traces across the request lifecycle

## 2. Why this matters

Distributed tracing explains where latency and failures occur across asynchronous boundaries that ordinary request logs cannot connect.

## 3. Previous work done

**Immediate dependencies:** [Day 006](step-006.md), [Day 017](step-017.md), [Day 022](step-022.md), [Day 023](step-023.md), [Day 024](step-024.md).

Before starting, all 24 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 24 earlier steps</summary>

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

</details>

## 4. How to build it

- Use current OpenTelemetry Python APIs and reviewed instrumentation packages; disable experimental instrumentation that captures bodies by default.
- Allow bounded attributes such as route, provider, model class, status, and config version; never request IDs as metric labels or prompt/response span attributes.
- Use links where async causality is not a strict parent-child relationship.
- Add an in-memory exporter test asserting sensitive attributes are absent.
- Keep collector endpoints configurable and local by default; verify current collector/Tempo images before adding the profile.

## 5. Workflow for today

1. Add tracing setup and lifecycle shutdown/flush.
2. Instrument synchronous and asynchronous seams.
3. Propagate context through outbox and Celery envelopes.
4. Run a synthetic request and inspect the complete trace.
5. Add privacy assertions and a collector smoke test.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
docker compose --profile observability up -d
.venv/Scripts/python -m pytest backend/tests/integration/test_tracing.py backend/tests/security/test_telemetry_privacy.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] One synthetic request correlates API, provider, outbox, and worker spans.
- [ ] Trace context survives redelivery and worker execution.
- [ ] No prompt, completion, API key, or provider credential appears in spans.
- [ ] Exporter shutdown flushes without blocking requests indefinitely.

## 8. Done and handoff

Complete the deliverable: **content-free distributed traces across the request lifecycle**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-025: added correlated OpenTelemetry traces across API, queue, and workers`

Next: [Day 026](step-026.md) — Expose Prometheus metrics and provision Grafana.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
