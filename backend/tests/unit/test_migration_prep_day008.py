"""Day 008 unit tests: migration *preparation* evidence for Day 009.

The step explicitly forbids applying an unreviewed schema, so nothing here
runs Alembic against the live database. Instead we prove that the Day 009
revision, when generated, will contain exactly the reviewed objects:

1. SQLAlchemy can render CREATE TABLE DDL for every new model.
2. Alembic's autogenerate comparator sees exactly the eight Day 008 tables
   as net additions against an empty schema (env.py's ``include_object`` rule
   keeps reflected objects such as ``schema_info`` out of the drop side).
3. The cross-tenant composite foreign keys survive DDL rendering.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.models import Base, metadata

DAY008_TABLES = {
    "organizations",
    "users",
    "role_assignments",
    "applications",
    "provider_configs",
    "provider_credentials",
    "provider_catalog_entries",
    "model_catalog_entries",
}


def _autogenerate_add_table_names(connection: sa.Connection) -> set[str]:
    context = MigrationContext.configure(connection=connection)
    diff = compare_metadata(context, metadata)
    return {entry[1].name for entry in diff if entry[0] == "add_table"}


def test_metadata_create_all_renders_every_day008_table() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        inspector = sa.inspect(engine)
        assert DAY008_TABLES <= set(inspector.get_table_names())
    finally:
        engine.dispose()


def test_autogenerate_previews_exactly_day008_additions() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            names = _autogenerate_add_table_names(connection)
    finally:
        engine.dispose()
    assert names == DAY008_TABLES


def test_composite_cross_tenant_fks_render_in_ddl() -> None:
    expected = {
        "role_assignments": "FOREIGN KEY(user_id, organization_id) REFERENCES users (id, organization_id)",
        "applications": "FOREIGN KEY(owner_user_id, organization_id) REFERENCES users (id, organization_id)",
    }
    for table_name, clause in expected.items():
        ddl = str(sa.schema.CreateTable(metadata.tables[table_name]))
        assert clause in ddl, table_name


def test_composite_fk_columns_are_marked_foreign() -> None:
    for table_name, fk_cols in (
        ("role_assignments", ("user_id", "organization_id")),
        ("applications", ("owner_user_id", "organization_id")),
    ):
        table = metadata.tables[table_name]
        composite = [
            constraint
            for constraint in table.constraints
            if isinstance(constraint, sa.ForeignKeyConstraint)
            and set(constraint.column_keys) == set(fk_cols)
            and len(constraint.column_keys) == 2
        ]
        assert composite, f"{table_name} lacks composite FK over {fk_cols}"
        assert len(composite) == 1, table_name
        ddl = str(sa.schema.CreateTable(table))
        assert "REFERENCES users (id, organization_id)" in ddl, table_name


def test_users_expose_composite_unique_key_for_cross_tenant_fks() -> None:
    """PostgreSQL/SQLite require the composite FK target to be a unique key."""
    users = metadata.tables["users"]
    unique_keys = [
        constraint
        for constraint in users.constraints
        if isinstance(constraint, sa.UniqueConstraint)
        and {col.name for col in constraint.columns} == {"id", "organization_id"}
    ]
    assert unique_keys, "users(id, organization_id) UNIQUE missing — composite FKs dangle"


def test_every_tenant_table_is_indexed_on_organization_id() -> None:
    for name in DAY008_TABLES - {
        "organizations",
        "provider_catalog_entries",
        "model_catalog_entries",
    }:
        table = metadata.tables[name]
        indexed_cols = {col.name for idx in table.indexes for col in idx.columns}
        assert "organization_id" in indexed_cols, name
