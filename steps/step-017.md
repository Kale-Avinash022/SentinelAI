# Day 017 — Record paid attempts and enforce idempotency

> Week 3 · Working LLM gateway · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Prevent duplicate local work and make provider billing attempts traceable before network execution.

- Add llm_requests and provider_attempt ledger records with client idempotency keys.
- Persist an attempt before calling the provider and capture provider request IDs when available.
- Define replay behavior for in-progress, completed, conflicting, and unknown outcomes.

**Deliverable:** a pre-call attempt ledger with tested tenant-scoped idempotency

## 2. Why this matters

A client retry after a lost response can otherwise cause a second paid generation; a database transaction alone cannot undo provider billing.

## 3. Previous work done

**Immediate dependencies:** [Day 009](step-009.md), [Day 010](step-010.md), [Day 013](step-013.md), [Day 014](step-014.md), [Day 015](step-015.md), [Day 016](step-016.md).

Before starting, all 16 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 16 earlier steps</summary>

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

</details>

## 4. How to build it

- Scope idempotency by organization/application/key and a canonical request fingerprint; enforce a unique constraint.
- Use atomic insert/claim semantics so concurrent identical requests cannot both call the provider.
- Store metadata and attempt state, not prompt bodies; optionally support a separately approved encrypted short-TTL response cache later.
- If the original body was not retained, return a documented replay-unavailable result with the original request ID rather than executing again.
- Record provider request ID, model, start time, and billing-relevant usage for reconciliation.

## 5. Workflow for today

1. Design the request/attempt state diagram and uniqueness rules.
2. Add migration and repository methods.
3. Integrate pre-call persistence into the gateway service.
4. Test concurrent duplicates, changed payload reuse, expiry, and completed replay without stored content.
5. Document exactly what idempotency guarantees and what it cannot guarantee.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
PYTHONPATH=backend .venv/Scripts/python -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python -m pytest backend/tests/integration/test_idempotency.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] An attempt is durable before the provider call.
- [ ] Concurrent identical submissions produce at most one provider invocation.
- [ ] Reusing a key with a different fingerprint conflicts.
- [ ] Missing replay content never causes a new paid call.

## 8. Done and handoff

Complete the deliverable: **a pre-call attempt ledger with tested tenant-scoped idempotency**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-017: added durable provider attempts and scoped idempotency claims`

Next: [Day 018](step-018.md) — Handle ambiguous provider outcomes safely.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
