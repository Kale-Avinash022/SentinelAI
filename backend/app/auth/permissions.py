"""Versioned four-role permission matrix (Day 008).

This module is the machine-readable counterpart of
``docs/architecture/ADR-0004-rbac-permission-matrix.md``. Day 012 will wire
these constants into FastAPI dependencies and Day 051 will reuse the approval
authorities here — nobody may invent role semantics later.

Rules encoded below:

- **read** — every role, including Viewer (read-only).
- **propose** — advisory only; even ``propose_recovery`` grants no execution
  authority (Zero Model Autonomy Rule applies to proposals from any actor).
- **approve** — Owner and Admin only. Operators may acknowledge and execute
  already-approved actions but cannot authorize Category B changes.
- **execute** — Owner, Admin and Operator, and only *after* the required
  approval step (Category A is preapproved by policy).
- **administer** — Owner owns org/billing/membership/key lifecycle; Admin
  delegates application and provider-configuration management only.
- Self-approval of high-risk (Category B) changes is configurable at the
  organization level but **default deny** (see :func:`self_approval_allowed`).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from app.auth.roles import ApprovalCategory, Role

#: Bump on any semantic change; ADR-0004 records the history.
PERMISSION_MATRIX_VERSION = 1

HIGH_RISK_CATEGORY = ApprovalCategory.B


class Permission(StrEnum):
    """Every guarded capability in the MVP, grouped by the five matrix verbs."""

    # read
    READ_TELEMETRY = "read:telemetry"
    READ_REQUEST_HISTORY = "read:requests"
    READ_INCIDENTS = "read:incidents"
    READ_AUDIT_LOGS = "read:audit"
    # propose
    PROPOSE_INCIDENT = "propose:incident"
    PROPOSE_RECOVERY = "propose:recovery"
    # approve
    APPROVE_HIGH_RISK = "approve:high-risk"  # Category B
    APPROVE_CONFIG_CHANGE = "approve:config"
    # execute
    ACKNOWLEDGE_INCIDENT = "execute:ack-incident"
    TRIGGER_APPROVED_FAILOVER = "execute:approved-failover"
    EXECUTE_APPROVED_ACTION = "execute:approved-action"
    # administer
    MANAGE_APPLICATIONS = "administer:applications"
    MANAGE_PROVIDER_CONFIG = "administer:provider-config"
    MANAGE_MEMBERS = "administer:members"
    MANAGE_API_KEYS = "administer:api-keys"
    MANAGE_BILLING = "administer:billing"
    DELETE_ORGANIZATION = "administer:delete-org"


_ALL = tuple(Permission)


def _viewer() -> frozenset[Permission]:
    return frozenset(
        {
            Permission.READ_TELEMETRY,
            Permission.READ_REQUEST_HISTORY,
            Permission.READ_INCIDENTS,
            Permission.READ_AUDIT_LOGS,
        }
    )


def _operator() -> frozenset[Permission]:
    return _viewer() | frozenset(
        {
            Permission.PROPOSE_INCIDENT,
            Permission.PROPOSE_RECOVERY,
            Permission.ACKNOWLEDGE_INCIDENT,
            Permission.TRIGGER_APPROVED_FAILOVER,
            Permission.EXECUTE_APPROVED_ACTION,
            Permission.READ_AUDIT_LOGS,
        }
    )


def _admin() -> frozenset[Permission]:
    return _operator() | frozenset(
        {
            Permission.APPROVE_HIGH_RISK,
            Permission.APPROVE_CONFIG_CHANGE,
            Permission.MANAGE_APPLICATIONS,
            Permission.MANAGE_PROVIDER_CONFIG,
        }
    )


#: role -> granted permissions. Frozen so no runtime code can mutate the matrix.
PERMISSION_MATRIX: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.OWNER: frozenset(_ALL),
        Role.ADMIN: _admin(),
        Role.OPERATOR: _operator(),
        Role.VIEWER: _viewer(),
    }
)

#: Roles whose holders may authorize a Category B (high-risk) recovery action.
#: Mirrors recovery-policy.md ("Approved by Admin/Owner").
APPROVAL_AUTHORITIES: Mapping[ApprovalCategory, frozenset[Role]] = MappingProxyType(
    {
        ApprovalCategory.A: frozenset(),  # automated within preapproved limits
        ApprovalCategory.B: frozenset({Role.OWNER, Role.ADMIN}),
        ApprovalCategory.C: frozenset(),  # manual-only; never workflow-approved
    }
)


def permissions_for(role: Role | str) -> frozenset[Permission]:
    """Granted permission set for a role name; raises ValueError if unknown."""
    return PERMISSION_MATRIX[Role(role)]


def has_permission(role: Role | str, permission: Permission | str) -> bool:
    """True when ``role`` grants ``permission``. Unknown inputs raise ValueError."""
    return Permission(permission) in permissions_for(role)


def can_approve_category(role: Role | str, category: ApprovalCategory | str) -> bool:
    """True when the role is an approval authority for the recovery category."""
    return Role(role) in APPROVAL_AUTHORITIES[ApprovalCategory(category)]


def self_approval_allowed(
    *,
    requestor_role: Role | str,
    approver_role: Role | str,
    category: ApprovalCategory | str,
    org_allow_self_approval: bool = False,
) -> bool:
    """Decide whether the same person may request and approve an action.

    Default deny: the organization flag only unlocks *Category B* self-approval
    for an Owner (e.g. a single-owner demo tenant); Admins never self-approve
    high-risk changes they requested, and Category C is manual-only regardless.
    Category A is preapproved by policy limits, so no human separation applies.
    """
    requestor = Role(requestor_role)
    approver = Role(approver_role)
    typed_category = ApprovalCategory(category)
    if typed_category is ApprovalCategory.A:
        return True  # automated within preapproved limits; no human approval step
    if not can_approve_category(approver, typed_category):
        return False
    if requestor is not approver:
        return True  # ordinary two-party approval is always allowed
    # Only Category B reaches here: A short-circuits above, C fails the
    # authority check (no role is a Category C approver — manual-only tier).
    return org_allow_self_approval and approver is Role.OWNER
