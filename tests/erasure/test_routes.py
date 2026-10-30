"""Customer erasure over HTTP: owner-only, CSRF-protected, address matched case-insensitively."""

from datetime import datetime
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from services.api.auth.store import InMemoryAuthStore
from services.api.erasure.service import ErasureService
from services.api.main import create_app
from tests.auth import conftest as auth
from tests.auth.conftest import csrf, login

clock = auth.clock
make_settings = auth.make_settings
owner_hash = auth.owner_hash
store = auth.store


class FakeEraser:
    def __init__(self, tickets: dict[str, int]) -> None:
        self.tickets = tickets
        self.calls: list[tuple[str, str]] = []

    async def erase_customer(self, email: str, actor: str, now: datetime) -> int:
        self.calls.append((email, actor))
        return self.tickets.pop(email, 0)


@pytest.fixture
def eraser() -> FakeEraser:
    return FakeEraser({"asha@example.com": 3})


def make_client(settings, clock, auth_store, eraser):  # type: ignore[no-untyped-def]
    app = create_app(
        auth_settings=settings,
        auth_store=auth_store,
        clock=clock,
        erasure_service=ErasureService(eraser, clock),
    )
    return TestClient(app)


@pytest.fixture
def client(make_settings, clock, store: InMemoryAuthStore, eraser):  # type: ignore[no-untyped-def]
    with make_client(make_settings(), clock, store, eraser) as test_client:
        login(test_client)
        yield test_client


def erase(client: TestClient, email: str):  # type: ignore[no-untyped-def]
    return client.delete(f"/api/v1/customers/{quote(email, safe='')}", headers=csrf(client))


def test_needs_a_signed_in_owner(make_settings, clock, store, eraser) -> None:  # type: ignore[no-untyped-def]
    with make_client(make_settings(), clock, store, eraser) as anonymous:
        response = anonymous.delete("/api/v1/customers/asha%40example.com")
    assert response.status_code in (401, 403)
    assert eraser.calls == []


def test_needs_the_csrf_token(client: TestClient, eraser: FakeEraser) -> None:
    response = client.delete("/api/v1/customers/asha%40example.com")
    assert response.status_code == 403
    assert eraser.calls == []


def test_erases_and_reports_the_count(client: TestClient, eraser: FakeEraser) -> None:
    response = erase(client, "asha@example.com")
    assert response.status_code == 200
    assert response.json() == {"erased": 3}
    assert eraser.calls[0][0] == "asha@example.com"


def test_the_address_is_trimmed_and_lowercased(client: TestClient, eraser: FakeEraser) -> None:
    assert erase(client, "  Asha@Example.COM ").json() == {"erased": 3}
    assert eraser.calls[0][0] == "asha@example.com"


def test_the_actor_is_the_signed_in_owner(client: TestClient, eraser: FakeEraser) -> None:
    erase(client, "asha@example.com")
    assert eraser.calls[0][1] == auth.OWNER_EMAIL


def test_an_unknown_or_already_erased_address_is_404(client: TestClient) -> None:
    assert erase(client, "nobody@example.com").status_code == 404
    assert erase(client, "asha@example.com").status_code == 200
    assert erase(client, "asha@example.com").status_code == 404


@pytest.mark.parametrize("bad", ["asha", "@example.com", "asha@", "asha@localhost", "a b@x.com"])
def test_something_that_is_not_an_address_is_422(
    client: TestClient, eraser: FakeEraser, bad: str
) -> None:
    assert erase(client, bad).status_code == 422
    assert eraser.calls == []


def test_without_a_database_erasure_is_unavailable(
    make_settings, clock, store, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(auth_settings=make_settings(), auth_store=store, clock=clock)
    with TestClient(app) as test_client:
        login(test_client)
        assert erase(test_client, "asha@example.com").status_code == 503
