# Day 010 — Enforce PostgreSQL row-level security

> Week 2 · Authentication, tenant isolation, and versioning · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Add a database-enforced organization boundary that survives pooled connections.

- Enable and force RLS on tenant-owned tables.
- Set organization context transaction-locally from authenticated server state.
- Add pool checkout/checkin safeguards and missing-context tests.

**Deliverable:** transaction-safe RLS with passing isolation tests

## 2. Why this matters

Filtering queries in Python is useful but insufficient; RLS provides a second boundary against forgotten WHERE clauses and identifier tampering.

## 3. Previous work done

**Immediate dependencies:** [Day 004](step-004.md), [Day 008](step-008.md), [Day 009](step-009.md).

Before starting, all 9 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 9 earlier steps</summary>

- [ ] [Day 001](step-001.md) — defined MVP scope, six modules, safety categories, and architecture decisions
- [ ] [Day 002](step-002.md) — initialized Git, minimal structure, .venv, locked dependencies, and smoke test
- [ ] [Day 003](step-003.md) — added repeatable lint, type, test, dependency, and secret checks
- [ ] [Day 004](step-004.md) — started local PostgreSQL/Redis with health checks and least-privilege roles
- [ ] [Day 005](step-005.md) — connected async SQLAlchemy and verified migration upgrade/downgrade behavior
- [ ] [Day 006](step-006.md) — built FastAPI app factory, versioned API, health checks, and safe errors
- [ ] [Day 007](step-007.md) — verified clean-setup bootstrap and closed foundation gaps
- [ ] [Day 008](step-008.md) — defined tenant models, provider catalog, and four-role permission matrix
- [ ] [Day 009](step-009.md) — applied constrained tenant schema, indexes, and least-privilege grants

</details>

## 4. How to build it

- Create policies comparing organization_id to current_setting with missing_ok behavior that denies rather than broadens access.
- Use SET LOCAL or set_config('app.organization_id', tenant_id, true) inside the same transaction as the query; never use session-wide tenant state that can leak through pooling.
- Run application queries as the non-owner sentinel_app role and FORCE ROW LEVEL SECURITY.
- Keep migration/admin tasks explicitly separate and audited; do not give the API a role that bypasses RLS.
- Reset context on connection return and test consecutive requests for different organizations on one pool.

## 5. Workflow for today

1. List every tenant-owned table and required operation.
2. Write policies and grants in a reviewed migration.
3. Implement a transaction-scoped session dependency.
4. Test organization A, organization B, missing context, and pooled reuse.
5. Verify EXPLAIN/queries still use intended tenant indexes.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
PYTHONPATH=backend .venv/Scripts/python -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python -m pytest backend/tests/integration/test_rls.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] RLS is enabled and forced on every tenant-owned table.
- [ ] Missing tenant context exposes no tenant rows and blocks unsafe writes.
- [ ] A pooled connection cannot carry organization A context into organization B.
- [ ] The API role cannot bypass policies or change them.

## 8. Done and handoff

Complete the deliverable: **transaction-safe RLS with passing isolation tests**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-010: enforced forced RLS with transaction-local tenant context`

Next: [Day 011](step-011.md) — Prove cross-tenant isolation with negative tests.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
