"""SQLAlchemy declarative base and shared metadata for all SentinelAI domain models.

Alembic autogenerate imports ``metadata`` from this package; every domain table
added on later days must inherit from :class:`app.models.base.Base` so it is
included in migration rendering. Tenant tables will additionally carry an
``org_id`` column and RLS policies (Week 2).
"""

from app.models.base import Base, metadata

__all__ = ["Base", "metadata"]
