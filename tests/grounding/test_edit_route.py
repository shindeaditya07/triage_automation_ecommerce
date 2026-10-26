"""POST /api/v1/tickets/{id}/draft/validate over an in-memory draft source."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.auth.store import InMemoryAuthStore
from services.api.grounding.edit import EditContext
from services.api.main import create_app
from tests.auth.conftest import csrf, login
from tests.grounding.conftest import ORDER, SIGN_OFF, order_cite, reply

TICKET = uuid4()


class Source:
    def __init__(self) -> None:
        self.asked: list[UUID] = []

    async def edit_context(self, ticket_id: UUID) -> EditContext | None:
        self.asked.append(ticket_id)
        if ticket_id != TICKET:
            return None
        return EditContext(
            draft_id=uuid4(),
            customer_email=ORDER["customer_email"],
            subject=None,
            body="Order RD-10001.",
            order=ORDER,
            citations=[order_cite("order_id")],
        )


@pytest.fixture
def client(make_settings, clock, store: InMemoryAuthStore, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STORE_SIGN_OFF", SIGN_OFF)
    app = create_app(
        auth_settings=make_settings(),
        auth_store=store,
        clock=clock,
        edit_context_source=Source(),
    )
    with TestClient(app) as test_client:
        yield test_client


def url(ticket: UUID = TICKET) -> str:
    return f"/api/v1/tickets/{ticket}/draft/validate"


def test_it_needs_a_signed_in_owner(client: TestClient) -> None:
    assert client.post(url(), json={"reply_text": "x"}).status_code in (401, 403)
    login(client)
    assert client.post(url(), json={"reply_text": "x"}, headers=csrf(client)).status_code == 200


def test_a_good_edit_passes(client: TestClient) -> None:
    login(client)
    body = {"reply_text": reply("Your order RD-10001 is with us.")}
    response = client.post(url(), json=body, headers=csrf(client))
    assert response.status_code == 200
    assert response.json() == {"passed": True, "failures": []}


def test_a_flawed_edit_is_a_200_with_the_failures(client: TestClient) -> None:
    login(client)
    response = client.post(
        url(), json={"reply_text": "We will refund you today."}, headers=csrf(client)
    )
    assert response.status_code == 200  # a warning never blocks
    body = response.json()
    assert body["passed"] is False
    assert {f["rule_id"] for f in body["failures"]} >= {"G-3", "G-8"}
    assert set(body["failures"][0]) == {"rule_id", "span", "detail", "citation_missing"}


def test_an_unknown_ticket_or_empty_text_is_rejected(client: TestClient) -> None:
    login(client)
    assert (
        client.post(url(uuid4()), json={"reply_text": "x"}, headers=csrf(client)).status_code == 404
    )
    assert client.post(url(), json={"reply_text": ""}, headers=csrf(client)).status_code == 422
    assert client.post(url(), json={}, headers=csrf(client)).status_code == 422
    huge = {"reply_text": "x" * 20_001}
    assert client.post(url(), json=huge, headers=csrf(client)).status_code == 422


def test_without_a_database_the_route_is_unavailable(make_settings, clock, store) -> None:  # type: ignore[no-untyped-def]
    app = create_app(auth_settings=make_settings(), auth_store=store, clock=clock)
    with TestClient(app) as test_client:
        login(test_client)
        response = test_client.post(url(), json={"reply_text": "x"}, headers=csrf(test_client))
        assert response.status_code == 503
