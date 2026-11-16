"""Role and permission vocabulary for the four-role RBAC model (Day 008).

The database stores roles as plain strings with CHECK constraints (not native
PostgreSQL ENUM types) so Day 009/010 migrations stay reversible and RLS
policies can compare against literal text. These enums are the single source
of truth for valid values; :mod:`app.auth.permissions` builds the versioned
permission matrix on top of them.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """The four standardized organization roles (brief §4)."""

    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class ApprovalCategory(StrEnum):
    """Recovery action tiers from docs/security/recovery-policy.md."""

    A = "A"
    B = "B"
    C = "C"


ROLE_VALUES: frozenset[str] = frozenset(role.value for role in Role)
APPROVAL_CATEGORY_VALUES: frozenset[str] = frozenset(cat.value for cat in ApprovalCategory)
