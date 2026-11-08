# Day 008 — Model organizations users applications and roles

> Week 2 · Authentication, tenant isolation, and versioning · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Define the multi-tenant domain and permission matrix before exposing protected data.

- Add UUID models for organizations, users, applications, and provider catalog entries.
- Define Owner/Admin/Operator/Viewer permissions and approval authority.
- Add constraints and tenant relationships matching the brief ER model.

**Deliverable:** reviewed tenant models and a versioned permission matrix

## 2. Why this matters

Authorization is a domain invariant, not a frontend convention; ambiguous ownership leads directly to cross-tenant leaks.

## 3. Previous work done

**Immediate dependencies:** [Day 001](step-001.md), [Day 005](step-005.md), [Day 007](step-007.md).

Before starting, all 7 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 7 earlier steps</summary>

- [ ] [Day 001](step-001.md) — defined MVP scope, six modules, safety categories, and architecture decisions
- [ ] [Day 002](step-002.md) — initialized Git, minimal structure, .venv, locked dependencies, and smoke test
- [ ] [Day 003](step-003.md) — added repeatable lint, type, test, dependency, and secret checks
- [ ] [Day 004](step-004.md) — started local PostgreSQL/Redis with health checks and least-privilege roles
- [ ] [Day 005](step-005.md) — connected async SQLAlchemy and verified migration upgrade/downgrade behavior
- [ ] [Day 006](step-006.md) — built FastAPI app factory, versioned API, health checks, and safe errors
- [ ] [Day 007](step-007.md) — verified clean-setup bootstrap and closed foundation gaps

</details>

## 4. How to build it

- Use organization_id on every tenant-owned row and composite foreign keys where they prevent cross-tenant references.
- Keep global provider metadata separate from organization-specific provider configuration and secret references.
- Define Owner organization administration, Admin applications/keys/policies and high-risk approval, Operator incidents/proposals/execution of approved actions, Viewer read-only.
- Make self-approval configurable but default deny for high-risk changes; seed two synthetic admins for demos.
- Write a permission matrix ADR now so Day 51 does not invent roles during recovery work.

## 5. Workflow for today

1. Translate the brief ER diagram into only the tables needed now.
2. Add SQLAlchemy models, enums/check constraints, and relationships.
3. Write pure unit tests for the role matrix.
4. Review every relationship for a possible organization mismatch.
5. Prepare migration generation for the next day without applying an unreviewed schema.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/unit/test_permissions.py -q
PYTHONPATH=backend .venv/Scripts/python -m alembic -c backend/alembic.ini check
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Every tenant-owned model has an organization boundary.
- [ ] The four-role matrix covers read, propose, approve, execute, and administer.
- [ ] Provider secrets are references, not JSON credentials.
- [ ] Model constraints reject invalid roles and ownership combinations.

## 8. Done and handoff

Complete the deliverable: **reviewed tenant models and a versioned permission matrix**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-008: defined tenant models, provider catalog, and four-role permission matrix`

Next: [Day 009](step-009.md) — Apply tenant schema migrations and database grants.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
