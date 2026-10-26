"""Readiness against a real Postgres: each component fails alone, and all together pass."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from typing import TypeVar

import asyncpg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from services.api import health
from services.api.main import create_app
from services.api.policy_source import PolicyExpectation, expectation

T = TypeVar("T")
VECTOR_ZERO = "('[' || array_to_string(array_fill(0, ARRAY[1536]), ',') || ']')::vector"


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _in_rolled_back_transaction(url: str, body: Callable[[AsyncConnection], Awaitable[T]]) -> T:
    async def run() -> T:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                await conn.begin()
                try:
                    return await body(conn)
                finally:
                    await conn.rollback()
        finally:
            await engine.dispose()

    return asyncio.run(run())


async def _seed(conn: AsyncConnection, ids: list[str], version: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO policy_embeddings"
            " (policy_id, policy_version, passage_text, embed_model, embedding)"
            f" SELECT unnest(CAST(:ids AS text[])), :version, 'x', 'fake', {VECTOR_ZERO}"
        ),
        {"ids": ids, "version": version},
    )


@pytest.fixture
def expected() -> PolicyExpectation:
    return expectation()


def test_migrations_check_passes_at_head(migrated_url: str, expected: PolicyExpectation) -> None:
    result = _in_rolled_back_transaction(
        migrated_url, lambda conn: health._check_migrations(conn, expected)
    )
    assert result is None


def test_migrations_check_names_the_revision_when_behind(
    migrated_url: str, expected: PolicyExpectation
) -> None:
    async def body(conn: AsyncConnection) -> str | None:
        await conn.execute(text("UPDATE alembic_version SET version_num = 'old000'"))
        return await health._check_migrations(conn, expected)

    result = _in_rolled_back_transaction(migrated_url, body)
    assert result is not None
    assert "old000" in result and health.head_revision() in result


def test_migrations_check_reports_an_unmigrated_database(
    migrated_url: str, expected: PolicyExpectation
) -> None:
    async def body(conn: AsyncConnection) -> str | None:
        await conn.execute(text("DELETE FROM alembic_version"))
        return await health._check_migrations(conn, expected)

    result = _in_rolled_back_transaction(migrated_url, body)
    assert result is not None and result.startswith("no migration applied")


def test_pgvector_check_passes_and_fails_when_the_extension_is_gone(
    migrated_url: str, expected: PolicyExpectation
) -> None:
    async def body(conn: AsyncConnection) -> tuple[str | None, str | None]:
        present = await health._check_pgvector(conn, expected)
        await conn.execute(text("DROP EXTENSION vector CASCADE"))
        return present, await health._check_pgvector(conn, expected)

    present, absent = _in_rolled_back_transaction(migrated_url, body)
    assert present is None
    assert absent == "the 'vector' extension is not installed"


def test_policy_index_check_reports_each_way_it_can_be_wrong(
    migrated_url: str, expected: PolicyExpectation
) -> None:
    ids = sorted(expected.policy_ids)

    async def body(conn: AsyncConnection) -> list[str | None]:
        results = [await health._check_policy_index(conn, expected)]  # empty
        await _seed(conn, ids, "0.1-stale")
        results.append(await health._check_policy_index(conn, expected))  # only stale rows
        await _seed(conn, ids[:-1], expected.version)
        results.append(await health._check_policy_index(conn, expected))  # one passage missing
        await _seed(conn, ids[-1:], expected.version)
        results.append(await health._check_policy_index(conn, expected))  # complete
        return results

    empty, stale, partial, complete = _in_rolled_back_transaction(migrated_url, body)
    assert empty is not None and "empty" in empty
    assert stale is not None and "0.1-stale" in stale and expected.version in stale
    assert partial == f"1 of {len(ids)} policy passages are not embedded"
    assert complete is None


def test_a_migrated_database_without_a_policy_index_is_degraded_on_that_alone(
    migrated_url: str,
) -> None:
    engine = create_async_engine(migrated_url, poolclass=NullPool)
    with TestClient(create_app(engine)) as client:
        response = client.get("/readyz")
    asyncio.run(engine.dispose())
    assert response.status_code == 503
    assert response.json()["failing"] == ["policy_index"]


@pytest.fixture
def committed_index(migrated_url: str, expected: PolicyExpectation) -> Iterator[None]:
    async def seed() -> None:
        conn = await asyncpg.connect(_dsn(migrated_url))
        try:
            await conn.execute(
                "INSERT INTO policy_embeddings"
                " (policy_id, policy_version, passage_text, embed_model, embedding)"
                f" SELECT unnest($1::text[]), $2, 'x', 'fake', {VECTOR_ZERO}",
                sorted(expected.policy_ids),
                expected.version,
            )
        finally:
            await conn.close()

    async def clear() -> None:
        conn = await asyncpg.connect(_dsn(migrated_url))
        try:
            await conn.execute("DELETE FROM policy_embeddings")
        finally:
            await conn.close()

    asyncio.run(seed())
    try:
        yield
    finally:
        asyncio.run(clear())


def test_readyz_is_200_when_every_component_is_healthy(
    migrated_url: str, committed_index: None
) -> None:
    engine = create_async_engine(migrated_url, poolclass=NullPool)
    with TestClient(create_app(engine)) as client:
        response = client.get("/readyz")
        health_response = client.get("/healthz")
    asyncio.run(engine.dispose())
    assert (response.status_code, response.json()) == (200, {"status": "ok"})
    assert health_response.status_code == 200


def _raising(_: AsyncConnection, __: PolicyExpectation) -> Awaitable[str | None]:
    raise RuntimeError("boom with a secret detail")


def test_a_check_that_raises_is_reported_by_class_name_and_the_others_still_run(
    migrated_url: str, expected: PolicyExpectation, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = tuple(
        (name, _raising if name == health.PGVECTOR else check) for name, check in health.CHECKS
    )
    monkeypatch.setattr(health, "CHECKS", checks)
    engine = create_async_engine(migrated_url, poolclass=NullPool)
    failures = asyncio.run(health._evaluate(engine, expected))
    asyncio.run(engine.dispose())
    assert failures["pgvector"] == "check failed (RuntimeError)"
    assert "boom" not in str(failures)
    assert health.DB not in failures and health.MIGRATIONS not in failures


def test_when_select_1_fails_the_other_components_are_not_checked(
    migrated_url: str, expected: PolicyExpectation, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = tuple((name, _raising if name == health.DB else c) for name, c in health.CHECKS)
    monkeypatch.setattr(health, "CHECKS", checks)
    engine = create_async_engine(migrated_url, poolclass=NullPool)
    failures = asyncio.run(health._evaluate(engine, expected))
    asyncio.run(engine.dispose())
    assert failures[health.DB] == "check failed (RuntimeError)"
    assert failures[health.PGVECTOR] == "not checked: database unreachable"
    assert set(failures) == set(health.COMPONENTS)
