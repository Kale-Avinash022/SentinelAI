# Day 019 — Build the provider router and request budgets

> Week 3 · Working LLM gateway · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Select an eligible configured provider while enforcing capability, privacy, latency, and cost boundaries.

- Implement static-policy routing over immutable configuration snapshots.
- Add per-request timeout/deadline and token/cost reservation logic.
- Return controlled no-eligible-provider errors without trying arbitrary destinations.

**Deliverable:** a deterministic compatible-provider router with request budgets

## 2. Why this matters

Failover is safe only when alternatives are compatible and budgeted; the cheapest or fastest model is not automatically an acceptable replacement.

## 3. Previous work done

**Immediate dependencies:** [Day 014](step-014.md), [Day 015](step-015.md), [Day 016](step-016.md), [Day 017](step-017.md), [Day 018](step-018.md).

Before starting, all 18 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 18 earlier steps</summary>

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

</details>

## 4. How to build it

- Resolve one immutable config snapshot per request and carry its version through telemetry/attempts.
- Filter by enabled status, capabilities, privacy class, quality floor, and approved provider/model allowlist before ranking.
- Reserve maximum configured cost/tokens atomically in Redis; reconcile actual usage later and define behavior when Redis is unavailable.
- Fail closed for paid calls when budget authority is unavailable; local fake mode may use an explicit test policy.
- Do not introduce dynamic health-based switching yet; Day 53 adds breaker/fallback after recovery gates exist.

## 5. Workflow for today

1. Define route eligibility and ranking as pure functions.
2. Implement snapshot resolution and budget reservation adapter.
3. Connect the router to the gateway without bypassing the attempt ledger.
4. Test incompatible fallback, exhausted budget, stale config, and Redis outage.
5. Document deterministic routing versus later approved failover.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/unit/test_router.py backend/tests/integration/test_budgets.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Only allowlisted compatible providers are eligible.
- [ ] One request uses one immutable configuration version.
- [ ] Budget reservations are atomic and reconciled or released.
- [ ] Unavailable budget authority cannot silently permit paid calls.

## 8. Done and handoff

Complete the deliverable: **a deterministic compatible-provider router with request budgets**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-019: built snapshot-based routing with capability, privacy, and budget checks`

Next: [Day 020](step-020.md) — Add streaming cancellation and tenant rate limits.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
