# Day 012 — Add dashboard login JWT sessions and RBAC

> Week 2 · Authentication, tenant isolation, and versioning · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Authenticate humans securely and enforce the permission matrix on the server.

- Add Argon2id password hashing and /api/v1/auth/login.
- Issue short-lived JWT access tokens and rotating refresh sessions.
- Protect refresh cookies with HttpOnly/SameSite/Secure settings and CSRF/origin checks.

**Deliverable:** revocable human authentication with tested RBAC

## 2. Why this matters

Human operators need revocable sessions and least privilege before incident or recovery controls exist.

## 3. Previous work done

**Immediate dependencies:** [Day 006](step-006.md), [Day 008](step-008.md), [Day 010](step-010.md), [Day 011](step-011.md).

Before starting, all 11 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 11 earlier steps</summary>

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

</details>

## 4. How to build it

- Store refresh-token hashes and expiry/revocation state in PostgreSQL; keep access tokens short-lived and validate issuer/audience/algorithm/expiry.
- Return access tokens to the frontend for memory-only storage; do not place them in localStorage.
- Rotate refresh tokens and revoke the family on reuse; use constant-time comparisons where relevant.
- Apply RBAC dependencies after authentication and organization resolution, never trust role claims without server-side membership checks.
- Use generic login errors and rate limiting to avoid account enumeration; seed demo users only in development.

## 5. Workflow for today

1. Implement password verification and session records.
2. Add login, refresh, logout, and current-user endpoints.
3. Wire authentication/authorization dependencies and cookie/origin settings.
4. Test invalid signatures, expiry, revoked refresh reuse, CSRF, and every role.
5. Document local HTTP versus production HTTPS cookie behavior.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/security/test_auth.py -q
.venv/Scripts/python -m pytest backend/tests/contract/test_auth.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Passwords use Argon2id and are never logged.
- [ ] Invalid/expired JWTs and reused refresh tokens are rejected.
- [ ] Refresh mutation requires the intended cookie plus CSRF/origin protection.
- [ ] Owner/Admin/Operator/Viewer permissions match the ADR.

## 8. Done and handoff

Complete the deliverable: **revocable human authentication with tested RBAC**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-012: added secure login, short JWT sessions, refresh rotation, and RBAC`

Next: [Day 013](step-013.md) — Manage applications API keys and append-only audit.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
