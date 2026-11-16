"""Day 008 unit tests: the versioned four-role permission matrix (pure, hermetic).

These tests pin ADR-0004 matrix v1 exactly: any silent widening of a role,
new unreviewed permission, or relaxed self-approval default fails the gate.
"""

from __future__ import annotations

from typing import Protocol

import pytest
import sqlalchemy as sa
from app.auth.permissions import (
    APPROVAL_AUTHORITIES,
    HIGH_RISK_CATEGORY,
    PERMISSION_MATRIX,
    PERMISSION_MATRIX_VERSION,
    Permission,
    can_approve_category,
    has_permission,
    permissions_for,
    self_approval_allowed,
)
from app.auth.roles import APPROVAL_CATEGORY_VALUES, ROLE_VALUES, ApprovalCategory, Role

VERB_PREFIXES = ("read:", "propose:", "approve:", "execute:", "administer:")


class _HasConstraints(Protocol):
    """Structural view of SQLAlchemy 2.1's typed ``__table__`` protocol."""

    @property
    def constraints(self) -> set[sa.Constraint]: ...


def test_matrix_covers_all_four_roles() -> None:
    assert set(PERMISSION_MATRIX) == {Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.VIEWER}
    assert ROLE_VALUES == {"owner", "admin", "operator", "viewer"}


def test_matrix_covers_all_five_verbs() -> None:
    all_permissions = set(PERMISSION_MATRIX[Role.OWNER])
    for prefix in VERB_PREFIXES:
        assert any(perm.value.startswith(prefix) for perm in all_permissions), prefix
    for perm in Permission:
        assert perm.value.startswith(VERB_PREFIXES), perm


def test_matrix_is_fully_populated_and_frozen() -> None:
    for role, granted in PERMISSION_MATRIX.items():
        assert isinstance(granted, frozenset), role
        assert set(granted) <= set(Permission), role
    with pytest.raises(TypeError):
        PERMISSION_MATRIX[Role.VIEWER] = frozenset()  # type: ignore[index]


def test_exact_matrix_v1_snapshot() -> None:
    """Complete v1 truth table — change ADR-0004 and bump version together."""
    expected: dict[Role, set[str]] = {
        Role.OWNER: {perm.value for perm in Permission},
        Role.ADMIN: {
            "read:telemetry",
            "read:requests",
            "read:incidents",
            "read:audit",
            "propose:incident",
            "propose:recovery",
            "approve:high-risk",
            "approve:config",
            "execute:ack-incident",
            "execute:approved-failover",
            "execute:approved-action",
            "administer:applications",
            "administer:provider-config",
        },
        Role.OPERATOR: {
            "read:telemetry",
            "read:requests",
            "read:incidents",
            "read:audit",
            "propose:incident",
            "propose:recovery",
            "execute:ack-incident",
            "execute:approved-failover",
            "execute:approved-action",
        },
        Role.VIEWER: {
            "read:telemetry",
            "read:requests",
            "read:incidents",
            "read:audit",
        },
    }
    assert PERMISSION_MATRIX_VERSION == 1
    assert {
        role: {perm.value for perm in grants} for role, grants in PERMISSION_MATRIX.items()
    } == expected


def test_viewer_is_purely_read_only() -> None:
    for perm in set(Permission) - set(PERMISSION_MATRIX[Role.VIEWER]):
        assert perm.value.startswith("read:") is False, perm
    assert all(
        not has_permission(Role.VIEWER, p) for p in Permission if not p.value.startswith("read:")
    )


def test_ownership_hierarchy_is_monotonic() -> None:
    """viewer ⊂ operator ⊂ admin ⊂ owner on granted permissions."""
    assert PERMISSION_MATRIX[Role.VIEWER] < PERMISSION_MATRIX[Role.OPERATOR]
    assert PERMISSION_MATRIX[Role.OPERATOR] < PERMISSION_MATRIX[Role.ADMIN]
    assert PERMISSION_MATRIX[Role.ADMIN] < PERMISSION_MATRIX[Role.OWNER]


def test_propose_grants_no_execution_authority() -> None:
    """Zero Model Autonomy corollary: proposing never implies approving/executing."""
    for role in Role:
        if Permission.PROPOSE_RECOVERY in PERMISSION_MATRIX[role]:
            assert Permission.APPROVE_HIGH_RISK not in PERMISSION_MATRIX[role] or role in (
                Role.OWNER,
                Role.ADMIN,
            )
    # Operator can propose a recovery but cannot approve the very category it targets.
    assert Permission.PROPOSE_RECOVERY in PERMISSION_MATRIX[Role.OPERATOR]
    assert Permission.APPROVE_HIGH_RISK not in PERMISSION_MATRIX[Role.OPERATOR]
    assert not can_approve_category(Role.OPERATOR, ApprovalCategory.B)


def test_high_risk_approval_authority_is_exactly_owner_and_admin() -> None:
    assert APPROVAL_AUTHORITIES[ApprovalCategory.B] == frozenset({Role.OWNER, Role.ADMIN})
    assert can_approve_category(Role.OWNER, "B")
    assert can_approve_category(Role.ADMIN, "B")
    assert not can_approve_category(Role.OPERATOR, "B")
    assert not can_approve_category(Role.VIEWER, "B")


def test_category_a_has_no_human_approver_and_category_c_has_no_automation() -> None:
    assert APPROVAL_AUTHORITIES[ApprovalCategory.A] == frozenset()
    assert APPROVAL_AUTHORITIES[ApprovalCategory.C] == frozenset()
    assert not can_approve_category(Role.OWNER, "C")
    assert not can_approve_category(Role.ADMIN, "C")
    assert APPROVAL_CATEGORY_VALUES == {"A", "B", "C"}
    assert HIGH_RISK_CATEGORY is ApprovalCategory.B


def test_self_approval_denied_by_default_for_high_risk() -> None:
    assert (
        self_approval_allowed(
            requestor_role=Role.OWNER, approver_role=Role.OWNER, category=ApprovalCategory.B
        )
        is False
    )
    assert (
        self_approval_allowed(
            requestor_role=Role.ADMIN, approver_role=Role.ADMIN, category=ApprovalCategory.B
        )
        is False
    )


def test_self_approval_opt_in_unlocks_owner_only() -> None:
    assert (
        self_approval_allowed(
            requestor_role=Role.OWNER,
            approver_role=Role.OWNER,
            category=ApprovalCategory.B,
            org_allow_self_approval=True,
        )
        is True
    )
    assert (
        self_approval_allowed(
            requestor_role=Role.ADMIN,
            approver_role=Role.ADMIN,
            category=ApprovalCategory.B,
            org_allow_self_approval=True,
        )
        is False
    )


def test_two_party_approval_and_non_high_risk_self_approval_allowed() -> None:
    # Admin requests, owner approves: always fine.
    assert (
        self_approval_allowed(
            requestor_role=Role.ADMIN, approver_role=Role.OWNER, category=ApprovalCategory.B
        )
        is True
    )
    # Non-authority approver is rejected regardless of parties.
    assert (
        self_approval_allowed(
            requestor_role=Role.OPERATOR, approver_role=Role.OPERATOR, category=ApprovalCategory.B
        )
        is False
    )
    # Category A is preapproved by policy: no human separation requirement.
    assert (
        self_approval_allowed(
            requestor_role=Role.OWNER, approver_role=Role.OWNER, category=ApprovalCategory.A
        )
        is True
    )


def test_unknown_roles_and_permissions_are_rejected() -> None:
    assert permissions_for("admin") == PERMISSION_MATRIX[Role.ADMIN]
    with pytest.raises(ValueError, match="admin3"):
        permissions_for("admin3")
    with pytest.raises(ValueError, match="superuser"):
        has_permission("superuser", Permission.READ_TELEMETRY)
    with pytest.raises(ValueError):
        has_permission(Role.VIEWER, "delete:everything")
    with pytest.raises(ValueError):
        can_approve_category(Role.OWNER, "D")


def test_matrix_module_is_the_single_source_for_role_strings() -> None:
    """Model CHECK constraints and the enum must agree (drift breaks Day 009)."""
    from app.models.user import RoleAssignment

    # SQLAlchemy 2.1 types ``__table__`` as a column-providing protocol that
    # omits ``constraints``; the runtime Table object always has it.
    table: _HasConstraints = RoleAssignment.__table__  # type: ignore[assignment]
    checks = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
        and constraint.name == "ck_role_assignments_valid_role"
    ]
    assert len(checks) == 1
    check_sql = str(checks[0].sqltext)
    for value in ROLE_VALUES:
        assert f"'{value}'" in check_sql
