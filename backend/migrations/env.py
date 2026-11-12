"""Alembic environment for SentinelAI — async engine, migration-owner DSN only.

Resolution order for the database URL:

1. ``sqlalchemy.url`` set programmatically (integration tests point this at a
   disposable database such as ``sentinel_ai_test``).
2. ``MIGRATION_DATABASE_URL`` from the environment / repo-root ``.env``
   (``sentinel_migrator`` — the schema owner). The application DSN is never
   consulted here; runtime code must not run DDL.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from app.models import metadata as target_metadata
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, async_engine_from_config

REPO_ROOT = Path(__file__).resolve().parents[2]

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return str(url)
    # CLI path: load local-only credentials from the ignored repo-root .env.
    load_dotenv(REPO_ROOT / ".env")
    migration_url = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not migration_url:
        raise RuntimeError(
            "No migration DSN: set MIGRATION_DATABASE_URL in the environment or .env, "
            "or pass sqlalchemy.url programmatically."
        )
    return migration_url


def _configure(connection: Connection | None, url: str) -> None:
    context.configure(
        url=url if connection is None else None,
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # Deliberate rendering: no implicit inclusion of anything outside metadata.
        include_object=lambda obj, name, type_, reflected, compare_to: not reflected,
    )


def run_migrations_offline() -> None:
    """Render SQL to stdout without a live connection ('alembic upgrade --sql')."""
    _configure(None, _resolve_url())
    context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    _configure(connection, "")
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against the live database through an async engine."""
    config.set_main_option("sqlalchemy.url", _resolve_url().replace("%", "%%"))
    connectable: AsyncEngine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
