# Day 011 — Prove cross-tenant isolation with negative tests

> Week 2 · Authentication, tenant isolation, and versioning · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Make tenant isolation a CI invariant rather than a one-time manual check.

- Create two synthetic organizations with overlapping resource identifiers and realistic data.
- Test list/detail/write paths and client-supplied organization IDs.
- Add pooled-session and raw-SQL negative tests under sentinel_app.

**Deliverable:** a repeatable cross-tenant negative-test suite

## 2. Why this matters

Most tenant leaks appear in forgotten filters, nested resources, background jobs, or connection reuse—not the obvious happy path.

## 3. Previous work done

**Immediate dependencies:** [Day 008](step-008.md), [Day 009](step-009.md), [Day 010](step-010.md).

Before starting, all 10 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 10 earlier steps</summary>

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

</details>

## 4. How to build it

- Use fixtures that create both organizations through the migration/setup path and query through the application role.
- Attempt direct object reference access, forged organization_id bodies, nested application references, and missing context.
- Assert indistinguishable not-found/forbidden behavior where existence leakage matters.
- Include a test that deliberately removes an application filter and still expects RLS to block foreign rows.
- Run these tests in CI against real PostgreSQL, not a mocked database.

## 5. Workflow for today

1. Build deterministic two-tenant fixtures.
2. Enumerate current protected operations and expected denials.
3. Add positive and negative integration tests.
4. Run repeated alternating-tenant requests to expose pool leaks.
5. Add the suite to the required CI gate and document the invariant.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/security/test_tenant_isolation.py -q
.venv/Scripts/python -m pytest backend/tests/integration/test_rls.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] No current endpoint returns or modifies another organization data.
- [ ] Forged organization IDs cannot override authenticated context.
- [ ] Missing context and pooled reuse are covered.
- [ ] CI uses the real non-owner PostgreSQL role.

## 8. Done and handoff

Complete the deliverable: **a repeatable cross-tenant negative-test suite**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-011: proved tenant isolation across APIs, writes, and pooled sessions`

Next: [Day 012](step-012.md) — Add dashboard login JWT sessions and RBAC.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
