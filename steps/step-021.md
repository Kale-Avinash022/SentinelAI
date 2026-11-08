# Day 021 — Verify the complete fake-provider gateway

> Week 3 · Working LLM gateway · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Checkpoint the first complete request path before adding asynchronous analytics.

- Run authenticated chat, streaming, idempotency, budget, and error scenarios end to end.
- Verify configuration snapshots and attempt metadata in PostgreSQL.
- Close Week 3 gaps and publish a gateway contract report.

**Deliverable:** a verified Week 3 gateway checkpoint

## 2. Why this matters

The brief explicitly requires a working gateway flow before risk, incidents, recovery, or dashboard complexity.

## 3. Previous work done

**Immediate dependencies:** [Day 012](step-012.md), [Day 013](step-013.md), [Day 014](step-014.md), [Day 015](step-015.md), [Day 016](step-016.md), [Day 017](step-017.md), [Day 018](step-018.md), [Day 019](step-019.md), [Day 020](step-020.md).

Before starting, all 20 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 20 earlier steps</summary>

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

</details>

## 4. How to build it

- Use two synthetic organizations and deterministic fake providers with fixed latency/usage.
- Exercise normal, slow, 429, 500, malformed, empty, reset, timeout, disconnect, and duplicate-key cases.
- Check every persisted request has organization/application/config/attempt correlation and no prompt body.
- Measure gateway overhead separately from fake provider generation time, but label it a preliminary measurement.
- Use remaining time for fixes rather than starting queue code with failing safety tests.

## 5. Workflow for today

1. Run the full non-paid test suite.
2. Execute a manual curl chat and streaming example against local fakes.
3. Inspect request/attempt rows and redacted logs.
4. Compare behavior with the brief contract and failure expectations.
5. Record actual results and unresolved issues in the gateway checkpoint.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests -m "not real_provider" -q
curl http://127.0.0.1:8000/openapi.json
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] The authenticated fake-backed request path works end to end.
- [ ] All Week 3 error/idempotency/streaming/budget tests pass.
- [ ] Primary request records contain metadata only.
- [ ] The checkpoint distinguishes measured behavior from targets.

## 8. Done and handoff

Complete the deliverable: **a verified Week 3 gateway checkpoint**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-021: verified authenticated gateway contracts, budgets, streaming, and idempotency`

Next: [Day 022](step-022.md) — Design telemetry events and the transactional outbox.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
