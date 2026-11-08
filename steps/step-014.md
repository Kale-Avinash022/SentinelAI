# Day 014 — Version provider configurations and prompts

> Week 2 · Authentication, tenant isolation, and versioning · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Create immutable configuration history and safe active-version selection before routing or rollback.

- Add provider/model configuration versions, prompt versions, and activation history.
- Store non-secret metadata plainly and sensitive prompt/config payloads through encrypted storage or secret references.
- Implement validation, redacted diff, and compare-and-swap activation.

**Deliverable:** immutable configuration/prompt history with safe activation

## 2. Why this matters

Rollback is meaningful only when every change is attributable, immutable, and compatible with the application current policy.

## 3. Previous work done

**Immediate dependencies:** [Day 001](step-001.md), [Day 008](step-008.md), [Day 010](step-010.md), [Day 012](step-012.md), [Day 013](step-013.md).

Before starting, all 13 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 13 earlier steps</summary>

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

</details>

## 4. How to build it

- Version fields include model capabilities, allowed providers, privacy class, quality floor, timeout/budget limits, evaluator version, and prompt hash/reference.
- Never put provider credentials in JSONB; store Secrets Manager/local secret references.
- Treat prompt bodies as sensitive configuration, not telemetry; use envelope encryption and access audit if stored.
- Activate by appending an activation record with expected previous version; concurrent changes must conflict rather than silently overwrite.
- Define last-known-good as a version that passed validation/evaluation, not merely the previous row; checkpoint Week 2 tests.

## 5. Workflow for today

1. Design immutable version and activation tables.
2. Add migrations, schemas, and encrypted payload adapter interface.
3. Implement create/validate/diff/activate services with audit.
4. Test concurrent activation, invalid compatibility, and secret leakage.
5. Run the Week 2 security suite and record the checkpoint.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
PYTHONPATH=backend .venv/Scripts/python -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python -m pytest backend/tests/unit/test_config_versions.py backend/tests/security -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Historical versions cannot be edited in place.
- [ ] Activation uses an expected-version check and produces audit history.
- [ ] Diffs redact sensitive values and credentials remain external references.
- [ ] Last-known-good requires explicit qualification evidence.

## 8. Done and handoff

Complete the deliverable: **immutable configuration/prompt history with safe activation**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-014: added immutable config/prompt versions, redacted diffs, and CAS activation`

Next: [Day 015](step-015.md) — Define LLM contracts and deterministic fake providers.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
