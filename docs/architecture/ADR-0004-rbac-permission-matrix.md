# ADR-0004: Four-Role Permission Matrix (Versioned, Server-Side)

> **Status:** Accepted
> **Date:** 2026-11-16 (Day 008)
> **Matrix version:** 1
> **Deciders:** SentinelAI core team
> **Related:** [Day 001 product brief](../product/project-brief.md) §4, [recovery A/B/C policy](../security/recovery-policy.md), Day 012 (JWT/RBAC wiring), Day 051 (approvals)

---

## Context

Authorization for a multi-tenant AI operations platform cannot be a frontend
convention or an ad-hoc check sprinkled around endpoints. If role semantics are
ambiguous, two failure modes follow directly: cross-tenant data leakage and
unauthorized recovery execution. Day 051 (expiring human approvals) must consume
a matrix that already exists — it may not invent roles at recovery time. This
ADR therefore freezes the role model **now**, as version 1 of the machine-readable
matrix in [`backend/app/auth/permissions.py`](../../backend/app/auth/permissions.py).

## Decision

### Roles

Four organization-scoped roles, stored per `role_assignments` row (one active
role per user, enforced by unique constraint and CHECK):

| Role | Persona | One-line contract |
|---|---|---|
| `owner` | Organization creator / billing owner | Full tenant control including destructive administration |
| `admin` | Engineering manager / DevOps lead | Applications, provider configuration, high-risk approval |
| `operator` | SRE / on-call engineer | Incidents, proposals, execution of already-approved actions |
| `viewer` | Developer / business analyst | Read-only |

### Permission Matrix v1

Verbs: **read**, **propose**, **approve**, **execute**, **administer**.
✅ = granted, — = denied.

| Permission | Owner | Admin | Operator | Viewer |
|---|---|---|---|---|
| `read:telemetry` | ✅ | ✅ | ✅ | ✅ |
| `read:requests` | ✅ | ✅ | ✅ | ✅ |
| `read:incidents` | ✅ | ✅ | ✅ | ✅ |
| `read:audit` | ✅ | ✅ | ✅ | ✅ |
| `propose:incident` | ✅ | ✅ | ✅ | — |
| `propose:recovery` | ✅ | ✅ | ✅ | — |
| `approve:high-risk` (Category B) | ✅ | ✅ | — | — |
| `approve:config` | ✅ | ✅ | — | — |
| `execute:ack-incident` | ✅ | ✅ | ✅ | — |
| `execute:approved-failover` | ✅ | ✅ | ✅ | — |
| `execute:approved-action` | ✅ | ✅ | ✅ | — |
| `administer:applications` | ✅ | ✅ | — | — |
| `administer:provider-config` | ✅ | ✅ | — | — |
| `administer:members` | ✅ | — | — | — |
| `administer:api-keys` | ✅ | — | — | — |
| `administer:billing` | ✅ | — | — | — |
| `administer:delete-org` | ✅ | — | — | — |

Invariants encoded in the matrix module and its unit tests:

1. **Propose grants no execution authority.** `propose:recovery` produces an
   advisory, typed incident proposal only; the Zero Model Autonomy Rule
   (recovery-policy.md §1) means neither LLMs nor any role-less actor can
   shortcut the approve → execute chain.
2. **Execute implies a prior approval step.** `execute:*` is meaningful only
   after the required approval exists (Category A is preapproved by policy
   limits; Category B requires a signed, expiring approval from `owner`/`admin`;
   Category C is manual-only and grants no automation permission to anyone).
3. **Approval authority for high risk is exactly `{owner, admin}`** —
   matching recovery-policy.md ("Approved by Admin/Owner").
4. **Destructive administration is owner-exclusive.** Member invites, API key
   lifecycle, billing and org deletion are never delegated to `admin`.

### Self-approval: configurable, default deny

- `organizations.allow_self_approval` defaults to **false**.
- When enabled, it unlocks *only* Category B self-approval for an **owner**
  (single-owner demo tenants); admins may never self-approve their own
  high-risk requests, and Category C stays manual-only regardless.
- Two-party approval (requestor ≠ approver, both authorities) is always valid.

### Schema-side ownership guarantees

- Every tenant-owned table carries a non-null `organization_id` (RLS target,
  Day 010).
- Composite foreign keys — `(user_id, organization_id) → users`,
  `(owner_user_id, organization_id) → users` — make cross-tenant ownership
  and role grants *structurally impossible*, not merely discouraged. Tenant
  validity inside those composite FKs is transitive through
  `users.organization_id → organizations.id` (one column, one SQL foreign
  key; the composite target is the `users(id, organization_id)` UNIQUE).
- Global provider/model catalog rows hold no `organization_id`; tenant
  `provider_configs` reference catalog providers by foreign key, and
  `provider_credentials` store `secret://` **references** only (CHECK-enforced
  prefix, JSON braces rejected) — never inline credentials.

## Consequences

- Day 012 JWT/RBAC dependencies **must** consume `PERMISSION_MATRIX` and
  `has_permission`; adding an endpoint permission means adding a `Permission`
  enum member plus matrix entries **and** bumping `PERMISSION_MATRIX_VERSION`
  with a table update in this ADR.
- Day 051 approvals read `APPROVAL_AUTHORITIES` and
  `self_approval_allowed(..., org_allow_self_approval=...)` — no new role
  concepts may appear in recovery code.
- Unit tests (`backend/tests/unit/test_permissions.py`) pin the whole matrix;
  any silent widening of a role fails the gate.
- Day 009 seeds two synthetic admins (plus one owner) in a synthetic demo
  organization via a data migration, so two-party Category-B approval is
  demonstrable without enabling `allow_self_approval` (per
  recovery-policy.md, approvals must never self-approve by default).

## Alternatives Rejected

- **Fine-grained capability strings per endpoint (CAs model).** Rejected for
  MVP: 84-day scope, and the four-role brief contract is portfolio-legible.
  The enum/matrix layout keeps this a mechanical later migration.
- **Native PostgreSQL ENUM types for roles.** Rejected: CHECK-constrained
  text keeps Day 009/010 migrations reversible and RLS policy literals simple.
- **Per-user role column instead of `role_assignments` rows.** Rejected:
  assignments give Day 012 an auditable, composite-FK-guarded join and leave
  room for future multi-org membership without schema rewrites.
