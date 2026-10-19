"""Real-Postgres fixtures. Point TEST_DATABASE_URL at a throwaway database with the pgvector
extension available (CI uses the pgvector/pgvector service container). The schema in that database
is dropped and rebuilt, so never aim this at a database you care about.

Without TEST_DATABASE_URL the tests skip locally. CI sets REQUIRE_DB=1, which turns a missing
database into a failure so the checks cannot silently stop running.
"""

import asyncio
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]

Statement = tuple[str, tuple[Any, ...]]


def _async_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail("REQUIRE_DB=1 but TEST_DATABASE_URL is not set")
        pytest.skip("TEST_DATABASE_URL not set")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _reset(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    return _async_url()


@pytest.fixture(scope="session")
def migrated_url(database_url: str) -> Iterator[str]:
    """A database with every migration applied."""
    asyncio.run(_reset(_dsn(database_url)))
    command.upgrade(alembic_config(database_url), "head")
    yield database_url


@pytest.fixture
def attempt(migrated_url: str) -> Callable[[list[Statement]], BaseException | None]:
    """Run statements in order inside one transaction that is always rolled back.

    Returns the first database error raised, or None if every statement succeeded.
    """

    async def _run(statements: list[Statement]) -> BaseException | None:
        conn = await asyncpg.connect(_dsn(migrated_url))
        tx = conn.transaction()
        await tx.start()
        try:
            for sql, args in statements:
                await conn.execute(sql, *args)
            return None
        except asyncpg.PostgresError as exc:
            return exc
        finally:
            await tx.rollback()
            await conn.close()

    return lambda statements: asyncio.run(_run(statements))


@pytest.fixture
def fetch(migrated_url: str) -> Callable[[str], list[asyncpg.Record]]:
    async def _run(sql: str) -> list[asyncpg.Record]:
        conn = await asyncpg.connect(_dsn(migrated_url))
        try:
            return await conn.fetch(sql)
        finally:
            await conn.close()

    return lambda sql: asyncio.run(_run(sql))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
