"""Retrying a failed ticket over HTTP: owner-only, CSRF-protected, 404 and 409 as documented."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.auth.store import InMemoryAuthStore
from services.api.main import create_app
from services.api.models.enums import TicketStatus
from services.api.retry.service import RetryService
from services.api.retry.store import NoSuchTicket, NotRetryable
from tests.auth import conftest as auth
from tests.auth.conftest import csrf, login

clock = auth.clock
make_settings = auth.make_settings
owner_hash = auth.owner_hash
store = auth.store

FAILED = uuid4()
FINE = uuid4()


class FakeRetrier:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    async def retry(self, ticket_id: UUID, actor: str, now: datetime) -> TicketStatus:
        if ticket_id == FAILED:
            self.calls.append((ticket_id, actor))
            return TicketStatus.CLASSIFIED
        if ticket_id == FINE:
            raise NotRetryable
        raise NoSuchTicket


@pytest.fixture
def retrier() -> FakeRetrier:
    return FakeRetrier()


def make_client(settings, clock, auth_store, retrier):  # type: ignore[no-untyped-def]
    app = create_app(
        auth_settings=settings,
        auth_store=auth_store,
        clock=clock,
        retry_service=RetryService(retrier, clock),
    )
    return TestClient(app)


@pytest.fixture
def client(make_settings, clock, store: InMemoryAuthStore, retrier):  # type: ignore[no-untyped-def]
    with make_client(make_settings(), clock, store, retrier) as test_client:
        login(test_client)
        yield test_client


def retry(client: TestClient, ticket_id: UUID):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/tickets/{ticket_id}/retry", headers=csrf(client))


def test_needs_a_signed_in_owner(make_settings, clock, store, retrier) -> None:  # type: ignore[no-untyped-def]
    with make_client(make_settings(), clock, store, retrier) as anonymous:
        response = anonymous.post(f"/api/v1/tickets/{FAILED}/retry")
    assert response.status_code in (401, 403)
    assert retrier.calls == []


def test_needs_the_csrf_token(client: TestClient, retrier: FakeRetrier) -> None:
    assert client.post(f"/api/v1/tickets/{FAILED}/retry").status_code == 403
    assert retrier.calls == []


def test_retries_a_failed_ticket_as_the_signed_in_owner(
    client: TestClient, retrier: FakeRetrier
) -> None:
    response = retry(client, FAILED)
    assert response.status_code == 200
    assert response.json() == {"ticket_id": str(FAILED), "status": "classified"}
    assert retrier.calls == [(FAILED, auth.OWNER_EMAIL)]


def test_a_ticket_that_has_not_failed_is_409(client: TestClient) -> None:
    assert retry(client, FINE).status_code == 409


def test_an_unknown_ticket_is_404(client: TestClient) -> None:
    assert retry(client, uuid4()).status_code == 404


def test_without_a_database_retry_is_unavailable(make_settings, clock, store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(auth_settings=make_settings(), auth_store=store, clock=clock)
    with TestClient(app) as test_client:
        login(test_client)
        assert retry(test_client, FAILED).status_code == 503
