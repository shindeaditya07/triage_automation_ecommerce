import asyncio
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from services.api import health
from services.api.health import COMPONENTS, Database, Readiness, check_readiness
from services.api.main import create_app
from services.api.policy_source import PolicyExpectation

# A host that cannot answer: nothing listens on port 1, so the connection is refused at once.
UNREACHABLE = (
    "postgresql+asyncpg://leak-user:leak-password@127.0.0.1:1/leak-db"  # pragma: allowlist secret
)


@pytest.fixture(autouse=True)
def _no_ambient_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # so a developer's .env cannot leak in


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_healthz_returns_ok_without_a_lifespan() -> None:
    response = TestClient(create_app()).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_api_starts_and_serves_healthz_with_no_database_configured(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_names_every_component_when_no_database_is_configured(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["failing"] == list(COMPONENTS)
    assert body["detail"]["db"] == "DATABASE_URL is not set"
    assert body["detail"]["pgvector"].startswith("not checked")


def test_a_malformed_database_url_is_reported_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://leak-user:leak-password@leak-host/leak-db",  # pragma: allowlist secret
    )
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["detail"]["db"] == "DATABASE_URL is not a valid asyncpg URL"
    assert "leak" not in response.text


def test_readyz_is_503_and_healthz_stays_200_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
        response = client.get("/readyz")
        assert client.get("/healthz").status_code == 200
    assert response.status_code == 503
    body = response.json()
    assert body["failing"] == list(COMPONENTS)
    assert body["detail"]["db"].startswith("cannot connect (")
    assert "leak" not in response.text and "127.0.0.1" not in response.text


def test_a_supplied_engine_is_used_and_left_for_the_caller_to_dispose() -> None:
    engine = create_async_engine(UNREACHABLE)
    with TestClient(create_app(engine)) as client:
        assert client.app.state.database.engine is engine  # type: ignore[attr-defined]
        assert client.get("/readyz").status_code == 503
    asyncio.run(engine.dispose())


def test_the_engine_built_from_the_environment_is_disposed_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    dispose = AsyncMock()
    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    with TestClient(create_app()) as client:
        assert client.app.state.database.engine is not None  # type: ignore[attr-defined]
        dispose.assert_not_awaited()
    dispose.assert_awaited_once()


def test_close_leaves_a_borrowed_engine_alone() -> None:
    engine = AsyncMock()
    asyncio.run(Database(engine=engine, owned=False).close())
    engine.dispose.assert_not_called()
    asyncio.run(Database(engine=None, problem="x").close())


def _database() -> Database:
    return Database(engine=AsyncMock(), owned=False)


def test_all_components_healthy_returns_the_bare_ok_body(monkeypatch: pytest.MonkeyPatch) -> None:
    async def healthy(*_: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(health, "_evaluate", healthy)
    monkeypatch.setattr(health, "expectation", lambda: PolicyExpectation("1", frozenset({"A-1.1"})))
    with TestClient(create_app(AsyncMock())) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_a_slow_check_times_out_and_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hangs(*_: object) -> dict[str, str]:
        await asyncio.sleep(5)
        return {}

    monkeypatch.setattr(health, "_evaluate", hangs)
    monkeypatch.setattr(health, "expectation", lambda: PolicyExpectation("1", frozenset()))
    result = asyncio.run(check_readiness(_database(), timeout=0.05))
    assert result.failing == list(COMPONENTS)
    assert result.detail["db"] == "timed out after 0.05s"


def test_an_unreadable_policies_file_fails_only_the_policy_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def healthy(*_: object) -> dict[str, str]:
        return {}

    def unreadable() -> PolicyExpectation:
        raise FileNotFoundError("docs/policies.md")

    monkeypatch.setattr(health, "_evaluate", healthy)
    monkeypatch.setattr(health, "expectation", unreadable)
    result = asyncio.run(check_readiness(_database()))
    assert result.failing == ["policy_index"]
    assert result.detail["policy_index"] == "policies.md is unreadable (FileNotFoundError)"


def test_public_body_lists_failing_components_only_when_degraded() -> None:
    assert Readiness(status="ok").public() == {"status": "ok"}
    degraded = Readiness(status="degraded", failing=["db"], detail={"db": "why"})
    assert degraded.public() == {"status": "degraded", "failing": ["db"], "detail": {"db": "why"}}
    assert not degraded.ready
