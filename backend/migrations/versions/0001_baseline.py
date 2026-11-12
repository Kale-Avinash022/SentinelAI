"""baseline: schema_info marker table

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-12

Intentional Day 005 baseline. Domain tables arrive on Day 008+; this first
revision exists to (a) stamp every environment with one current revision,
(b) prove the upgrade/downgrade round trip on a real object, and (c) record
the schema lineage in-database for operators.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schema_info",
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_schema_info")),
        schema=None,
    )
    op.bulk_insert(
        sa.table(
            "schema_info",
            sa.column("version", sa.Text),
            sa.column("description", sa.Text),
        ),
        [
            {
                "version": "1",
                "description": "Day 005 baseline - async SQLAlchemy + Alembic pipeline verified",
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("schema_info", schema=None)
