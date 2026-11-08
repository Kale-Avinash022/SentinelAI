# Day 013 — Manage applications API keys and append-only audit

> Week 2 · Authentication, tenant isolation, and versioning · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Create tenant-scoped machine credentials and record security-relevant mutations accountablely.

- Implement application CRUD and API-key create/list/revoke/rotate endpoints.
- Hash high-entropy keys, show raw values once, and support expiry/scopes.
- Add append-only audit records for key and application changes.

**Deliverable:** scoped API-key lifecycle and transactional append-only audit

## 2. Why this matters

Machine access must be attributable to an organization/application, and credential lifecycle events need trustworthy history.

## 3. Previous work done

**Immediate dependencies:** [Day 008](step-008.md), [Day 009](step-009.md), [Day 010](step-010.md), [Day 012](step-012.md).

Before starting, all 12 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 12 earlier steps</summary>

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

</details>

## 4. How to build it

- Generate keys with a cryptographically secure random source; store a lookup prefix plus hash, not reversible plaintext.
- Resolve organization/application from the stored key record, never from a client organization header alone.
- Return the raw key only in the creation response and redact it from logs/audit.
- Restrict the application DB role to INSERT/SELECT on audit; use a separate retention role for later expiry deletion.
- Use cursor pagination and organization-scoped authorization on every list/detail/mutation.

## 5. Workflow for today

1. Add application endpoints and schemas.
2. Implement key generation, hashing, rotation overlap, expiry, and revocation.
3. Add audit writing in the same transaction as the protected mutation.
4. Test raw-once behavior, revoked/expired keys, cross-tenant access, and audit update/delete denial.
5. Record synthetic key IDs, never raw keys, in test evidence.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/security/test_api_keys.py -q
.venv/Scripts/python -m pytest backend/tests/integration/test_audit.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Raw API keys appear only once and never in persistent logs/audit.
- [ ] Expired/revoked/foreign keys cannot access resources.
- [ ] Rotation supports a deliberate overlap and revocation is immediate.
- [ ] The application role cannot update or delete audit rows.

## 8. Done and handoff

Complete the deliverable: **scoped API-key lifecycle and transactional append-only audit**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-013: implemented application/key lifecycle, hashed credentials, and append-only audit`

Next: [Day 014](step-014.md) — Version provider configurations and prompts.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
