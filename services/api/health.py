"""Readiness (`/readyz`): can this instance do useful work right now?

Four components, each reported by name when it fails: the database, the pgvector extension, the
migration state and the policy index. Liveness (`/healthz`) is separate and never gets here.

A component that cannot be verified is reported as failing, never assumed healthy: if the database
is unreachable the other three are listed as failing too, with "not checked" as the reason.
Failure reasons carry exception class names and revision or policy versions, never URLs, hosts or
messages from the driver, because the response is public.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.api.db.engine import create_engine
from services.api.models.common import StrictModel
from services.api.policy_source import PolicyExpectation, expectation
from services.api.settings import DatabaseSettings

DB = "db"
PGVECTOR = "pgvector"
MIGRATIONS = "migrations"
POLICY_INDEX = "policy_index"
COMPONENTS = (DB, PGVECTOR, MIGRATIONS, POLICY_INDEX)
READINESS_TIMEOUT_SECONDS = 5.0
ALEMBIC_DIR = Path(__file__).resolve().parent / "alembic"


class Readiness(StrictModel):
    status: Literal["ok", "degraded"]
    failing: list[str] = Field(default_factory=list)
    detail: dict[str, str] = Field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status == "ok"

    def public(self) -> dict[str, object]:
        """The response body: just the status when healthy, the failing parts when not."""
        if self.ready:
            return {"status": "ok"}
        return {"status": "degraded", "failing": self.failing, "detail": self.detail}


@dataclass
class Database:
    """The engine readiness uses, or the reason there isn't one."""

    engine: AsyncEngine | None
    problem: str | None = None
    owned: bool = True  # False when a test supplied the engine and will dispose it

    async def close(self) -> None:
        if self.engine is not None and self.owned:
            await self.engine.dispose()


def open_database() -> Database:
    """Build the engine from DATABASE_URL. A missing or malformed URL is reported by `/readyz`,
    not raised, so the process still starts and `/healthz` stays up."""
    try:
        settings = DatabaseSettings()  # type: ignore[call-arg]
    except ValidationError as exc:
        missing = any(error["type"] == "missing" for error in exc.errors())
        reason = "DATABASE_URL is not set" if missing else "DATABASE_URL is not a valid asyncpg URL"
        return Database(engine=None, problem=reason)
    return Database(engine=create_engine(settings.database_url))


@lru_cache(maxsize=1)
def head_revision() -> str:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("no Alembic revisions found")
    return head


Check = Callable[[AsyncConnection, PolicyExpectation], Awaitable[str | None]]


async def _check_db(conn: AsyncConnection, _: PolicyExpectation) -> str | None:
    await conn.execute(text("SELECT 1"))
    return None


async def _check_pgvector(conn: AsyncConnection, _: PolicyExpectation) -> str | None:
    found = await conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
    return None if found else "the 'vector' extension is not installed"


async def _check_migrations(conn: AsyncConnection, _: PolicyExpectation) -> str | None:
    head = head_revision()
    result = await conn.execute(text("SELECT version_num FROM alembic_version"))
    rows = [str(row[0]) for row in result.all()]
    if rows == [head]:
        return None
    if not rows:
        return f"no migration applied; the code expects {head}"
    return f"database is at {', '.join(sorted(rows))}; the code expects {head}"


async def _check_policy_index(conn: AsyncConnection, expected: PolicyExpectation) -> str | None:
    result = await conn.execute(text("SELECT policy_id, policy_version FROM policy_embeddings"))
    rows = [(str(policy_id), str(version)) for policy_id, version in result.all()]
    current = {policy_id for policy_id, version in rows if version == expected.version}
    if not rows:
        return f"the policy index is empty; policies.md is version {expected.version}"
    if not current:
        built = sorted({version for _, version in rows})
        return f"index built from version {', '.join(built)}; policies.md is {expected.version}"
    missing = expected.policy_ids - current
    if missing:
        return f"{len(missing)} of {len(expected.policy_ids)} policy passages are not embedded"
    return None


CHECKS: tuple[tuple[str, Check], ...] = (
    (DB, _check_db),
    (PGVECTOR, _check_pgvector),
    (MIGRATIONS, _check_migrations),
    (POLICY_INDEX, _check_policy_index),
)


def _all_failing(reason: str) -> dict[str, str]:
    return {DB: reason, **{name: "not checked: database unreachable" for name in COMPONENTS[1:]}}


async def _evaluate(engine: AsyncEngine, expected: PolicyExpectation) -> dict[str, str]:
    try:
        conn = await engine.connect()
    except Exception as exc:
        return _all_failing(f"cannot connect ({type(exc).__name__})")
    failures: dict[str, str] = {}
    try:
        for name, check in CHECKS:
            try:
                # A savepoint keeps one failed statement from poisoning the checks after it.
                async with conn.begin_nested():
                    reason = await check(conn, expected)
            except Exception as exc:
                reason = f"check failed ({type(exc).__name__})"
            if reason is not None:
                failures[name] = reason
            if name == DB and reason is not None:
                return failures | {n: "not checked: database unreachable" for n in COMPONENTS[1:]}
    finally:
        await conn.close()
    return failures


def _policy_expectation() -> PolicyExpectation | str:
    try:
        return expectation()
    except (OSError, ValueError) as exc:
        return f"policies.md is unreadable ({type(exc).__name__})"


async def check_readiness(
    database: Database, timeout: float = READINESS_TIMEOUT_SECONDS
) -> Readiness:
    if database.engine is None:
        failures = _all_failing(database.problem or "no database configured")
    else:
        expected = _policy_expectation()
        try:
            if isinstance(expected, str):
                # Still check the database parts; only the policy index cannot be judged.
                failures = await asyncio.wait_for(
                    _evaluate(database.engine, PolicyExpectation("", frozenset())), timeout
                )
                failures[POLICY_INDEX] = expected
            else:
                failures = await asyncio.wait_for(_evaluate(database.engine, expected), timeout)
        except TimeoutError:
            failures = _all_failing(f"timed out after {timeout:g}s")
    if not failures:
        return Readiness(status="ok")
    failing = [name for name in COMPONENTS if name in failures]
    return Readiness(
        status="degraded", failing=failing, detail={name: failures[name] for name in failing}
    )
