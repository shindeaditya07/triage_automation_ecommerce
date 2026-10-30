"""`POST /api/v1/tickets/bulk`: owner-only, CSRF-protected, a bad row never stops the others."""

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.auth.store import InMemoryAuthStore
from services.api.clock import utc_now
from services.api.intake.service import IntakeService
from services.api.main import create_app
from services.api.models.enums import Channel
from services.api.settings import AuthSettings, IntakeSettings
from tests.auth.conftest import OWNER_EMAIL, OWNER_PASSWORD, SECRET_KEY, csrf
from tests.intake.conftest import FakeStore, form_body

BULK = "/api/v1/tickets/bulk"


def row(**overrides: Any) -> dict[str, Any]:
    return form_body(order_id="RD-10001") | overrides


def batch(*rows: dict[str, Any], batch_id: Any = None) -> dict[str, Any]:
    return {"batch_id": str(batch_id or uuid4()), "complaints": list(rows)}


@pytest.fixture
def anonymous_client(owner_hash: str, store: FakeStore) -> Iterator[TestClient]:
    auth = AuthSettings(  # type: ignore[call-arg]
        _env_file=None,
        secret_key=SECRET_KEY,
        owner_email=OWNER_EMAIL,
        owner_password_hash=owner_hash,
    )
    settings = IntakeSettings(_env_file=None, form_message_max_chars=200)  # type: ignore[call-arg]
    app = create_app(
        auth_settings=auth,
        auth_store=InMemoryAuthStore(),
        intake_settings=settings,
        intake_service=IntakeService(store, utc_now),
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def owner_client(anonymous_client: TestClient) -> TestClient:
    login = {"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    assert anonymous_client.post("/api/v1/auth/login", json=login).status_code == 200
    return anonymous_client


def upload(client: TestClient, body: dict[str, Any]):  # type: ignore[no-untyped-def]
    return client.post(BULK, json=body, headers=csrf(client))


def test_needs_a_signed_in_owner(anonymous_client: TestClient, store: FakeStore) -> None:
    assert anonymous_client.post(BULK, json=batch(row())).status_code in (401, 403)
    assert store.received == []


def test_needs_the_csrf_token(owner_client: TestClient, store: FakeStore) -> None:
    assert owner_client.post(BULK, json=batch(row())).status_code == 403
    assert store.received == []


def test_every_row_becomes_a_form_ticket(owner_client: TestClient, store: FakeStore) -> None:
    body = batch(
        row(message="Where is my order?"),
        row(email="b@example.com", message="It arrived broken.", order_id=None, subject=None),
    )
    response = upload(owner_client, body)
    assert response.status_code == 200
    assert response.json() == {"received": 2, "duplicates": 0, "rejected": []}
    first, second = (r.request for r in store.received)
    assert (first.channel, first.order_id_hint, first.body) == (
        Channel.FORM,
        "RD-10001",
        "Where is my order?",
    )
    assert (second.customer.email, second.subject, second.order_id_hint) == (
        "b@example.com",
        None,
        None,
    )


def test_more_than_the_rate_limit_is_fine_for_the_owner(
    owner_client: TestClient, store: FakeStore
) -> None:
    rows = [row(message=f"Complaint number {n}") for n in range(40)]
    assert upload(owner_client, batch(*rows)).json()["received"] == 40
    assert len(store.received) == 40


def test_a_bad_row_is_reported_by_number_and_the_rest_go_on(
    owner_client: TestClient, store: FakeStore
) -> None:
    body = batch(
        row(message="Fine one"),
        row(email="not-an-email"),
        row(message="   "),
        row(message="x" * 201),
        row(unknown_field="x"),
        row(message="Fine two"),
    )
    result = upload(owner_client, body).json()
    assert result["received"] == 2
    assert [r["row"] for r in result["rejected"]] == [2, 3, 4, 5]
    reasons = {r["row"]: r["reason"] for r in result["rejected"]}
    assert reasons[2].startswith("email")
    assert reasons[3] == "message is empty"
    assert reasons[4] == "message is too long"
    assert "x" * 50 not in str(result)  # a rejected row's text is never echoed back
    assert [r.request.body for r in store.received] == ["Fine one", "Fine two"]


def test_sending_the_same_batch_again_makes_no_new_tickets(
    owner_client: TestClient, store: FakeStore
) -> None:
    body = batch(row(message="One"), row(message="Two"))
    assert upload(owner_client, body).json()["received"] == 2
    again = upload(owner_client, body).json()
    assert (again["received"], again["duplicates"]) == (0, 2)
    assert len(store.received) == 2


def test_a_row_cannot_switch_off_the_honeypot_or_add_attachments(
    owner_client: TestClient, store: FakeStore
) -> None:
    body = batch(row(website="bot", attachments=[{"filename": "a.jpg"}]))
    result = upload(owner_client, body).json()
    assert result["received"] == 0
    assert result["rejected"][0]["reason"] == "unknown field: attachments, website"
    assert store.received == []


@pytest.mark.parametrize(
    "body",
    [
        {"complaints": [{"email": "a@example.com", "message": "x"}]},  # no batch id
        {"batch_id": "nope", "complaints": []},
        {"batch_id": str(uuid4()), "complaints": []},
        {"batch_id": str(uuid4()), "complaints": [row(message="x")] * 251},
        {"batch_id": str(uuid4())},
    ],
)
def test_a_malformed_request_is_a_422(owner_client: TestClient, body: dict[str, Any]) -> None:
    assert upload(owner_client, body).status_code == 422
