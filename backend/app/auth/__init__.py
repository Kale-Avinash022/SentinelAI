"""Domain authorization vocabulary: roles, permissions and the versioned matrix.

Day 012 adds runtime authentication modules (Argon2 hashing, JWT, FastAPI
dependencies) to this package; they must consume the constants defined here
rather than re-deriving role semantics.
"""

from app.auth.permissions import (
    APPROVAL_AUTHORITIES,
    PERMISSION_MATRIX,
    PERMISSION_MATRIX_VERSION,
    Permission,
    can_approve_category,
    has_permission,
    permissions_for,
    self_approval_allowed,
)
from app.auth.roles import APPROVAL_CATEGORY_VALUES, ROLE_VALUES, ApprovalCategory, Role

__all__ = [
    "APPROVAL_AUTHORITIES",
    "APPROVAL_CATEGORY_VALUES",
    "PERMISSION_MATRIX",
    "PERMISSION_MATRIX_VERSION",
    "ROLE_VALUES",
    "ApprovalCategory",
    "Permission",
    "Role",
    "can_approve_category",
    "has_permission",
    "permissions_for",
    "self_approval_allowed",
]
