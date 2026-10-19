"""The same store contract the in-memory store passes, run against real Postgres."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from services.api.auth.store import AuthStore, PostgresAuthStore
from tests.auth.conftest import OWNER_EMAIL, OWNER_PASSWORD, SECRET_KEY, WRONG_PASSWORD
from tests.auth.store_contract import StoreContract


class TestPostgresStore(StoreContract):
    @pytest.fixture
    async def store(self, migrated_url: str) -> AsyncIterator[AuthStore]:
        engine = create_async_engine(migrated_url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM auth_refresh_tokens"))
            await conn.execute(
                text("UPDATE auth_lockout SET consecutive_failures = 0, locked_until = NULL")
            )
        try:
            yield PostgresAuthStore(engine)
        finally:
            await engine.dispose()


def test_the_lockout_row_exists_after_migrating(migrated_url: str) -> None:
    import asyncio

    async def count() -> int:
        engine = create_async_engine(migrated_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                return int(await conn.scalar(text("SELECT count(*) FROM auth_lockout")) or 0)
        finally:
            await engine.dispose()

    assert asyncio.run(count()) == 1


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO auth_lockout (id) VALUES (2)",
        "UPDATE auth_lockout SET consecutive_failures = -1",
        "INSERT INTO auth_refresh_tokens (jti, sid, issued_at, expires_at)"
        " VALUES (gen_random_uuid(), gen_random_uuid(), now(), now())",
    ],
)
def test_the_auth_tables_reject_impossible_rows(attempt, statement: str) -> None:  # type: ignore[no-untyped-def]
    assert attempt([(statement, ())]) is not None


def test_login_refresh_and_logout_work_end_to_end_on_postgres(migrated_url: str) -> None:
    """The whole HTTP flow with the real store: state survives between requests in the database."""
    from fastapi.testclient import TestClient

    from services.api.auth.passwords import hash_password
    from services.api.main import create_app
    from services.api.settings import AuthSettings

    engine = create_async_engine(migrated_url, poolclass=NullPool)
    settings = AuthSettings(  # type: ignore[call-arg]
        _env_file=None,
        secret_key=SECRET_KEY,
        owner_email=OWNER_EMAIL,
        owner_password_hash=hash_password(OWNER_PASSWORD),
    )
    with TestClient(create_app(engine, auth_settings=settings)) as client:
        creds = {"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        wrong = {**creds, "password": WRONG_PASSWORD}
        assert client.post("/api/v1/auth/login", json=wrong).status_code == 401
        assert client.post("/api/v1/auth/login", json=creds).status_code == 200
        headers = {"X-CSRF-Token": client.cookies.get("rd_csrf") or ""}
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/refresh", headers=headers).status_code == 200
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/v1/auth/me").status_code == 401
    import asyncio

    asyncio.run(engine.dispose())
