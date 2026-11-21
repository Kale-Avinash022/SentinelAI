"""SQLAlchemy declarative base and shared metadata for all SentinelAI domain models.

Alembic autogenerate imports ``metadata`` from this package; every domain table
added on later days must inherit from :class:`app.models.base.Base` so it is
included in migration rendering. Tenant tables carry an ``organization_id``
column and (from Day 010) RLS policies keyed on it.

Import order is deterministic (org -> user -> application -> catalog) so
cross-module string relationship targets always resolve during mapper
configuration; keep new modules in dependency order.
"""

from app.models.api_key import KEY_SCOPE_VALUES, ApiKey, KeyScope
from app.models.application import APPLICATION_STATUS_VALUES, Application, ApplicationStatus
from app.models.audit import AUDIT_ACTOR_TYPE_VALUES, AuditActorType, AuditLog
from app.models.base import Base, metadata
from app.models.config_version import ConfigActivation, ConfigVersion
from app.models.provider_catalog import (
    SECRET_REF_PREFIX,
    CredentialStatus,
    ModelCatalogEntry,
    ProviderCatalogEntry,
    ProviderConfig,
    ProviderCredential,
)
from app.models.session import RefreshSession
from app.models.tenant import Organization
from app.models.user import RoleAssignment, User

__all__ = [
    "APPLICATION_STATUS_VALUES",
    "AUDIT_ACTOR_TYPE_VALUES",
    "KEY_SCOPE_VALUES",
    "ApiKey",
    "Application",
    "ApplicationStatus",
    "AuditActorType",
    "AuditLog",
    "Base",
    "ConfigActivation",
    "ConfigVersion",
    "CredentialStatus",
    "KeyScope",
    "ModelCatalogEntry",
    "Organization",
    "ProviderCatalogEntry",
    "ProviderConfig",
    "ProviderCredential",
    "RefreshSession",
    "RoleAssignment",
    "SECRET_REF_PREFIX",
    "User",
    "metadata",
]
