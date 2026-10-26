import asyncio
from collections.abc import Callable
from typing import Any

import asyncpg
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.ext.asyncio import create_async_engine

from services.api.db.tables import metadata
from tests.db.conftest import alembic_config

EXPECTED_TABLES = {
    "alembic_version",
    "audit_log",
    "auth_lockout",
    "auth_refresh_tokens",
    "drafts",
    "jobs",
    "order_snapshots",
    "policy_embeddings",
    "tickets",
}


def _table_names(fetch: Callable[[str], list[asyncpg.Record]]) -> set[str]:
    rows = fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return {row["tablename"] for row in rows}


def test_upgrade_creates_every_table(
    migrated_url: str, fetch: Callable[[str], list[asyncpg.Record]]
) -> None:
    assert _table_names(fetch) == EXPECTED_TABLES


def test_vector_extension_is_installed(fetch: Callable[[str], list[asyncpg.Record]]) -> None:
    assert fetch("SELECT 1 FROM pg_extension WHERE extname = 'vector'")


def test_migrations_match_table_definitions(migrated_url: str) -> None:
    """tables.py and the hand-written migration must describe the same schema."""

    async def diff() -> list[Any]:
        engine = create_async_engine(migrated_url)

        def _compare(sync_conn: Any) -> list[Any]:
            context = MigrationContext.configure(sync_conn, opts={"compare_type": True})
            return list(compare_metadata(context, metadata))

        try:
            async with engine.connect() as conn:
                return await conn.run_sync(_compare)
        finally:
            await engine.dispose()

    assert asyncio.run(diff()) == []


def test_downgrade_then_upgrade_is_clean(
    migrated_url: str, fetch: Callable[[str], list[asyncpg.Record]]
) -> None:
    config = alembic_config(migrated_url)
    command.downgrade(config, "base")
    assert _table_names(fetch) == {"alembic_version"}
    command.upgrade(config, "head")
    assert _table_names(fetch) == EXPECTED_TABLES


def test_every_declared_check_and_unique_constraint_exists(
    migrated_url: str, fetch: Callable[[str], list[asyncpg.Record]]
) -> None:
    """Alembic's comparison skips CHECK constraints, so compare their names directly."""
    declared = {
        constraint.name
        for table in metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint | UniqueConstraint)
    }
    present = {row["conname"] for row in fetch("SELECT conname FROM pg_constraint")}
    assert declared
    assert declared - present == set()
