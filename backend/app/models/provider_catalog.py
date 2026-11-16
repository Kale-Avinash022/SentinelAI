"""Global provider catalog vs. organization-specific configuration and secrets.

Deliberate split (Day 008 rule: *keep global provider metadata separate from
organization-specific provider configuration and secret references*):

* ``provider_catalog_entries`` / ``model_catalog_entries`` — **global**, no
  ``organization_id``: what exists in the world (providers, models, list
  prices). Readable by every tenant, editable by platform tooling only.
* ``provider_configs`` — **tenant**: how one organization routes to a global
  provider (aliases, budgets, enablement).
* ``provider_credentials`` — **tenant**: *references* to secrets
  (``secret://`` locators). Provider API keys are never stored as JSON
  columns or plaintext anywhere in this schema; hashing/enveloping arrives
  with Day 013 key management and Day 061 privacy modes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import new_id, utcnow
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.tenant import Organization

# A locator *scheme*, never a credential: secret_ref values point at where a
# secret lives (env/secret store), and the table CHECK rejects payload-shaped
# values. Bandit's B105 keyword heuristic cannot tell the difference.
SECRET_REF_PREFIX = "secret://"  # nosec


class CredentialStatus(StrEnum):
    """Lifecycle of a provider credential reference."""

    ACTIVE = "active"
    ROTATING = "rotating"
    REVOKED = "revoked"


class ProviderCatalogEntry(Base):
    """Global registry of supported providers (deterministic fakes included)."""

    __tablename__ = "provider_catalog_entries"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    supports_streaming: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    supports_chat: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    is_deterministic_mock: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )

    model_entries: Mapped[list[ModelCatalogEntry]] = relationship(
        back_populates="provider_entry", cascade="all, delete-orphan"
    )


class ModelCatalogEntry(Base):
    """Global per-model capabilities and *list* prices (per 1K tokens, USD).

    The foreign key onto ``provider_catalog_entries(provider)`` keeps model
    rows bound to a real provider row — tenant configs can reference a model
    by name and still resolve within catalog integrity.
    """

    __tablename__ = "model_catalog_entries"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    context_window_tokens: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    input_price_per_1k_tokens: Mapped[float] = mapped_column(sa.Numeric(12, 6), nullable=False)
    output_price_per_1k_tokens: Mapped[float] = mapped_column(sa.Numeric(12, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["provider"],
            ["provider_catalog_entries.provider"],
            name="fk_model_catalog_entries_provider_provider_catalog_entries",
        ),
        sa.UniqueConstraint(
            "provider", "model_name", name="uq_model_catalog_entries_provider_model"
        ),
        sa.CheckConstraint("context_window_tokens > 0", name="positive_context_window"),
        sa.CheckConstraint("input_price_per_1k_tokens >= 0", name="non_negative_input_price"),
        sa.CheckConstraint("output_price_per_1k_tokens >= 0", name="non_negative_output_price"),
    )

    provider_entry: Mapped[ProviderCatalogEntry] = relationship(back_populates="model_entries")


class ProviderConfig(Base):
    """One organization's routing posture toward a global provider+model pair."""

    __tablename__ = "provider_configs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("applications.id"), nullable=True, index=True
    )
    is_enabled: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    monthly_budget_usd: Mapped[float | None] = mapped_column(sa.Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        # Bound the config to a *global* catalog provider row (not just a string).
        sa.ForeignKeyConstraint(
            ["provider"],
            ["provider_catalog_entries.provider"],
            name="fk_provider_configs_provider_provider_catalog_entries",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            "model_name",
            name="uq_provider_configs_org_provider_model",
        ),
        sa.CheckConstraint(
            "monthly_budget_usd IS NULL OR monthly_budget_usd >= 0", name="non_negative_budget"
        ),
    )

    organization: Mapped[Organization] = relationship(back_populates="provider_configs")
    application: Mapped[Application | None] = relationship(back_populates="provider_configs")
    catalog_entry: Mapped[ProviderCatalogEntry] = relationship(
        "ProviderCatalogEntry",
        primaryjoin="foreign(ProviderConfig.provider) == ProviderCatalogEntry.provider",
        viewonly=True,
        uselist=False,
    )


class ProviderCredential(Base):
    """Reference to one organization's secret for a global provider.

    ``secret_ref`` is a locator (e.g. ``secret://env/OPENAI_API_KEY``), never
    the credential itself; CHECK enforces the scheme so a JSON blob or raw
    key can never be smuggled into this column.
    """

    __tablename__ = "provider_credentials"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    secret_ref: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=CredentialStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["provider"],
            ["provider_catalog_entries.provider"],
            name="fk_provider_credentials_provider_provider_catalog_entries",
        ),
        sa.UniqueConstraint(
            "organization_id", "provider", name="uq_provider_credentials_org_provider"
        ),
        sa.CheckConstraint(
            f"secret_ref LIKE '{SECRET_REF_PREFIX}%' AND secret_ref NOT LIKE '%{{%'",
            name="secret_ref_is_locator_not_payload",
        ),
        sa.CheckConstraint(
            f"status IN ({', '.join(sorted(repr(value.value) for value in CredentialStatus))})",
            name="valid_credential_status",
        ),
    )

    organization: Mapped[Organization] = relationship(back_populates="provider_credentials")
