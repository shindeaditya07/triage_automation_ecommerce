from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.api.auth.passwords import hash_password
from services.api.auth.store import InMemoryAuthStore
from services.api.main import create_app
from services.api.settings import AuthSettings

OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "correct horse battery staple"  # pragma: allowlist secret
WRONG_PASSWORD = "not the password"  # pragma: allowlist secret
SECRET_KEY = "k" * 40  # pragma: allowlist secret
START = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture(scope="session")
def owner_hash() -> str:
    return hash_password(OWNER_PASSWORD)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store() -> InMemoryAuthStore:
    return InMemoryAuthStore()


@pytest.fixture
def make_settings(owner_hash: str) -> Callable[..., AuthSettings]:
    def make(**overrides: Any) -> AuthSettings:
        values: dict[str, Any] = {
            "secret_key": SECRET_KEY,
            "owner_email": OWNER_EMAIL,
            "owner_password_hash": owner_hash,
            "environment": "local",
        }
        return AuthSettings(_env_file=None, **(values | overrides))  # type: ignore[call-arg]

    return make


@pytest.fixture
def make_client(
    clock: FakeClock, store: InMemoryAuthStore, make_settings: Callable[..., AuthSettings]
) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def make(**overrides: Any) -> TestClient:
        app = create_app(auth_settings=make_settings(**overrides), auth_store=store, clock=clock)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


def login(client: TestClient, email: str = OWNER_EMAIL, password: str = OWNER_PASSWORD) -> Any:
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("rd_csrf") or ""}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
