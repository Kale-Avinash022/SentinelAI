# Day 005 — Connect SQLAlchemy and create the Alembic baseline

> Week 1 · Foundation and architecture · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Establish async database access and versioned schema changes before adding domain tables.

- Configure SQLAlchemy 2 async engine/session lifecycle.
- Add Alembic with an intentional baseline migration.
- Test upgrade, downgrade in an isolated test database, and connection cleanup.

**Deliverable:** async database infrastructure and a tested Alembic baseline

## 2. Why this matters

A controlled migration path and pooled sessions prevent schema drift and connection leaks as the data model grows.

## 3. Previous work done

**Immediate dependencies:** [Day 002](step-002.md), [Day 004](step-004.md).

Before starting, all 4 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 4 earlier steps</summary>

- [ ] [Day 001](step-001.md) — defined MVP scope, six modules, safety categories, and architecture decisions
- [ ] [Day 002](step-002.md) — initialized Git, minimal structure, .venv, locked dependencies, and smoke test
- [ ] [Day 003](step-003.md) — added repeatable lint, type, test, dependency, and secret checks
- [ ] [Day 004](step-004.md) — started local PostgreSQL/Redis with health checks and least-privilege roles

</details>

## 4. How to build it

- Place engine/session code in backend/app/infrastructure/database.py and migrations under backend/migrations.
- Read separate migration and application DSNs; application code must never connect as schema owner.
- Use short transactions and explicit commit/rollback; later tenant context will use SET LOCAL inside those transactions.
- Configure Alembic to import metadata from backend/app/models and render constraints/indexes deliberately.
- Test migrations against a disposable database, not by dropping the development database.

## 5. Workflow for today

1. Add locked SQLAlchemy async driver and Alembic dependencies.
2. Configure engine creation and session dependency without connecting at import time.
3. Generate the baseline and inspect the SQL rather than trusting autogenerate blindly.
4. Run upgrade and a controlled downgrade/upgrade round trip.
5. Add an integration test that opens and closes sessions repeatedly.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
PYTHONPATH=backend .venv/Scripts/python -m alembic -c backend/alembic.ini upgrade head
PYTHONPATH=backend .venv/Scripts/python -m alembic -c backend/alembic.ini current
.venv/Scripts/python -m pytest backend/tests/integration/test_database.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Alembic upgrade is repeatable and records one current revision.
- [ ] The round-trip migration test uses an isolated database.
- [ ] Application connections use sentinel_app, migrations use sentinel_migrator.
- [ ] Sessions and connections are returned cleanly to the pool.

## 8. Done and handoff

Complete the deliverable: **async database infrastructure and a tested Alembic baseline**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-005: connected async SQLAlchemy and verified migration upgrade/downgrade behavior`

Next: [Day 006](step-006.md) — Build the FastAPI application shell.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
