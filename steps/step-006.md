# Day 006 — Build the FastAPI application shell

> Week 1 · Foundation and architecture · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Create the modular monolith entry point with versioned routing, health semantics, and safe errors.

- Add an app factory, /api/v1 router, liveness and readiness endpoints.
- Add typed settings, request correlation IDs, structured metadata-only logging, and a standard error envelope.
- Wire database/Redis readiness without making liveness depend on every dependency.

**Deliverable:** a running FastAPI shell with documented health and error contracts

## 2. Why this matters

Consistent contracts and observability at the edge make every later module easier to integrate and debug.

## 3. Previous work done

**Immediate dependencies:** [Day 001](step-001.md), [Day 002](step-002.md), [Day 004](step-004.md), [Day 005](step-005.md).

Before starting, all 5 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 5 earlier steps</summary>

- [ ] [Day 001](step-001.md) — defined MVP scope, six modules, safety categories, and architecture decisions
- [ ] [Day 002](step-002.md) — initialized Git, minimal structure, .venv, locked dependencies, and smoke test
- [ ] [Day 003](step-003.md) — added repeatable lint, type, test, dependency, and secret checks
- [ ] [Day 004](step-004.md) — started local PostgreSQL/Redis with health checks and least-privilege roles
- [ ] [Day 005](step-005.md) — connected async SQLAlchemy and verified migration upgrade/downgrade behavior

</details>

## 4. How to build it

- Use backend/app/main.py, api/v1, core, and infrastructure; keep domain services out of route handlers.
- Define the brief error shape with code, message, request_id, retryable; map validation/auth/provider failures without leaking stack traces.
- Validate settings at startup and bound request body size before expensive parsing.
- Liveness reports process health; readiness reports whether this instance can safely serve its configured dependencies.
- Generate OpenAPI from Pydantic models and add contract tests for headers and error envelopes.

## 5. Workflow for today

1. Implement settings and the app factory.
2. Add correlation middleware and exception handlers.
3. Add health/readiness and a versioned placeholder-free router containing only implemented endpoints.
4. Test success, validation failure, dependency outage, and request ID propagation.
5. Open the local docs page and inspect the generated contract.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
PYTHONPATH=backend .venv/Scripts/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
.venv/Scripts/python -m pytest backend/tests/contract -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Liveness and readiness have distinct documented behavior.
- [ ] Errors use one envelope and include a correlation/request ID.
- [ ] Oversized or invalid input is rejected consistently.
- [ ] Logs contain metadata, not request bodies or credentials.

## 8. Done and handoff

Complete the deliverable: **a running FastAPI shell with documented health and error contracts**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-006: built FastAPI app factory, versioned API, health checks, and safe errors`

Next: [Day 007](step-007.md) — Verify the foundation from a clean setup.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
