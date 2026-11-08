# Day 018 — Handle ambiguous provider outcomes safely

> Week 3 · Working LLM gateway · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Make timeout and lost-response behavior explicit instead of silently retrying uncertain paid work.

- Add UNKNOWN_OUTCOME state and controlled client responses.
- Build a metadata-only reconciliation workflow using provider request IDs and usage evidence.
- Test timeout before response, provider success/client disconnect, and missing provider identifiers.

**Deliverable:** tested ambiguous-outcome handling and reconciliation

## 2. Why this matters

A timeout is not evidence that execution failed. Safe cost behavior sometimes requires manual reconciliation rather than automatic retry.

## 3. Previous work done

**Immediate dependencies:** [Day 015](step-015.md), [Day 016](step-016.md), [Day 017](step-017.md).

Before starting, all 17 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 17 earlier steps</summary>

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

</details>

## 4. How to build it

- Classify pre-execution connection failures separately from ambiguous read timeouts after an attempt was sent.
- Never automatically replay UNKNOWN attempts; expose status and a safe operator reconciliation action.
- If a provider offers genuine idempotency semantics, verify current documentation and bind them to the exact request; do not assume support.
- Reconciliation updates metadata from evidence and audit, without inventing token usage or cost.
- Return retryable flags that describe safe client behavior, not merely the HTTP status.

## 5. Workflow for today

1. Extend the attempt state machine and error contract.
2. Add fake scenarios where the provider succeeds but the response is lost.
3. Implement reconciliation service and restricted CLI/API operation.
4. Test that unknown attempts are never selected for automatic retry.
5. Write the interview explanation of database vs provider-side exactly-once limits.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/integration/test_ambiguous_outcomes.py backend/tests/security/test_reconciliation.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Ambiguous attempts become UNKNOWN, not successful or safely retryable.
- [ ] Client retries cannot trigger a second provider call for UNKNOWN work.
- [ ] Reconciliation requires evidence, authorization, and audit.
- [ ] Unknown usage/cost remains labeled unknown until resolved.

## 8. Done and handoff

Complete the deliverable: **tested ambiguous-outcome handling and reconciliation**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-018: handled unknown paid outcomes without unsafe automatic replay`

Next: [Day 019](step-019.md) — Build the provider router and request budgets.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
