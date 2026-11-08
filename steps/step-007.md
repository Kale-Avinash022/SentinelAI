# Day 007 — Verify the foundation from a clean setup

> Week 1 · Foundation and architecture · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Prove another machine can reproduce the foundation and use this day as the first catch-up buffer.

- Write a root README quickstart for the implemented foundation only.
- Run a clean-clone/clean-venv bootstrap exercise.
- Repair any Week 1 gaps and record a foundation checkpoint report.

**Deliverable:** a reproducible Week 1 foundation and checkpoint record

## 2. Why this matters

A project that works only in one terminal is not reproducible; early rehearsal exposes hidden global dependencies.

## 3. Previous work done

**Immediate dependencies:** [Day 001](step-001.md), [Day 002](step-002.md), [Day 003](step-003.md), [Day 004](step-004.md), [Day 005](step-005.md), [Day 006](step-006.md).

Before starting, all 6 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 6 earlier steps</summary>

- [ ] [Day 001](step-001.md) — defined MVP scope, six modules, safety categories, and architecture decisions
- [ ] [Day 002](step-002.md) — initialized Git, minimal structure, .venv, locked dependencies, and smoke test
- [ ] [Day 003](step-003.md) — added repeatable lint, type, test, dependency, and secret checks
- [ ] [Day 004](step-004.md) — started local PostgreSQL/Redis with health checks and least-privilege roles
- [ ] [Day 005](step-005.md) — connected async SQLAlchemy and verified migration upgrade/downgrade behavior
- [ ] [Day 006](step-006.md) — built FastAPI app factory, versioned API, health checks, and safe errors

</details>

## 4. How to build it

- Use a temporary clone outside the working tree and a fresh .venv; do not delete the original environment.
- Follow only committed instructions and locked files, noting every undocumented manual step.
- Verify named volumes and role bootstrap are reproducible without copying local .env secrets.
- Keep the quickstart honest: endpoints/features not yet implemented belong in the roadmap, not the feature list.
- Use spare time to fix failures rather than starting authentication early.

## 5. Workflow for today

1. Review all Week 1 acceptance checks.
2. Create a temporary clean clone and fresh virtual environment.
3. Follow the README from dependency installation through API smoke tests.
4. Record deviations and update instructions/locks.
5. Remove only temporary artifacts you created after preserving useful reports.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
git status --short
bash scripts/check.sh
docker compose config
docker compose --profile core up -d
.venv/Scripts/python -m pytest backend/tests -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] A clean clone reaches healthy dependencies and passing tests without global pip installs.
- [ ] The README contains no real secrets or claimed future features.
- [ ] All Week 1 unresolved issues are fixed or explicitly carried into the next checkpoint.
- [ ] Temporary verification artifacts do not pollute the repository.

## 8. Done and handoff

Complete the deliverable: **a reproducible Week 1 foundation and checkpoint record**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-007: verified clean-setup bootstrap and closed foundation gaps`

Next: [Day 008](step-008.md) — Model organizations users applications and roles.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
