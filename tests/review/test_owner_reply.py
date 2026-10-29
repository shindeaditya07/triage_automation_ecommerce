"""The owner answers an escalated ticket themselves: checked like an edit, recorded, mock-sent."""

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.grounding.edit import EditContext
from services.api.grounding.sign_off import with_sign_off
from services.api.models.enums import TicketStatus
from services.api.review.store import EscalatedState
from tests.auth.conftest import csrf
from tests.grounding.conftest import ORDER, SIGN_OFF
from tests.review.conftest import GOOD, TICKET, FakeReviewStore

PLAIN = "Hello, I am sorry about this. I have read your message myself and will write to you."
SENT = with_sign_off(PLAIN, SIGN_OFF)  # the store's sign-off is added when it is missing
PROMISE = "We will refund you today."  # G-3
WRONG_ORDER = "We will refund you today. Your order RD-99999 has shipped."  # G-1, G-3, G-6


def escalated(**changes: object) -> EscalatedState:
    state = EscalatedState(
        ticket_status=TicketStatus.ESCALATED,
        escalation_reason="legal_threat",
        snapshot_id=None,
        edit=EditContext(
            draft_id=uuid4(),
            customer_email=ORDER["customer_email"],
            subject="Refund still missing",
            body="I want my refund or I will go to court.",
            order=ORDER,
            citations=[],
        ),
    )
    return replace(state, **changes)  # type: ignore[arg-type]


def reply(client: TestClient, body: dict, ticket=TICKET):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/tickets/{ticket}/owner-reply", json=body, headers=csrf(client))


@pytest.fixture
def pending(review_store: FakeReviewStore) -> FakeReviewStore:
    review_store.escalated = escalated()
    return review_store


def test_an_owner_reply_is_recorded_and_mock_sent(
    client: TestClient, pending: FakeReviewStore
) -> None:
    response = reply(client, {"reply_text": PLAIN, "original_viewed": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["ticket_status"], body["review_status"], body["final_text"]) == (
        "sent",
        "approved",
        SENT,
    )
    assert pending.events == ["owner reply by owner@example.com", "sent"]


def test_the_original_message_must_have_been_viewed(
    client: TestClient, pending: FakeReviewStore
) -> None:
    response = reply(client, {"reply_text": PLAIN})
    assert response.status_code == 409
    assert "original message" in response.json()["detail"]
    assert pending.events == []


def test_only_an_escalated_ticket_takes_an_owner_reply(
    client: TestClient, review_store: FakeReviewStore
) -> None:
    review_store.escalated = escalated(ticket_status=TicketStatus.DRAFTED)
    response = reply(client, {"reply_text": PLAIN, "original_viewed": True})
    assert response.status_code == 409
    assert review_store.events == []


def test_an_unknown_ticket_is_404(client: TestClient, pending: FakeReviewStore) -> None:
    assert reply(client, {"reply_text": PLAIN, "original_viewed": True}, uuid4()).status_code == 404


def test_a_reply_with_warnings_needs_an_override(
    client: TestClient, pending: FakeReviewStore
) -> None:
    response = reply(client, {"reply_text": PROMISE, "original_viewed": True})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert {f["rule_id"] for f in detail["failures"]} == {"G-3"}
    assert pending.events == []  # nothing was recorded


def test_a_covering_override_lets_the_reply_through_and_is_recorded(
    client: TestClient, pending: FakeReviewStore
) -> None:
    body = {
        "reply_text": PROMISE,
        "original_viewed": True,
        "override": {"reason": "I will refund this myself", "overridden_rules": ["G-3"]},
    }
    assert reply(client, body).status_code == 200
    [override] = pending.overrides
    assert override.actor == "owner@example.com"
    assert override.overridden_rules == ["G-3"]


def test_an_override_must_cover_every_rule_that_fired(
    client: TestClient, pending: FakeReviewStore
) -> None:
    body = {
        "reply_text": WRONG_ORDER,
        "original_viewed": True,
        "override": {"reason": "I checked", "overridden_rules": ["G-3"]},
    }
    response = reply(client, body)
    assert response.status_code == 422
    assert "does not cover" in response.json()["detail"]["message"]
    assert pending.events == []


def test_a_ticket_is_answered_once(client: TestClient, pending: FakeReviewStore) -> None:
    body = {"reply_text": PLAIN, "original_viewed": True}
    assert reply(client, body).status_code == 200
    assert reply(client, body).status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"reply_text": "", "original_viewed": True},
        {"reply_text": "x" * 20_001, "original_viewed": True},
        {"reply_text": PLAIN, "original_viewed": True, "surprise": 1},
    ],
)
def test_malformed_replies_are_422(
    client: TestClient, pending: FakeReviewStore, body: dict
) -> None:
    assert reply(client, body).status_code == 422
    assert pending.events == []


def test_an_anonymous_caller_is_refused(make_settings, clock, store, review_store) -> None:  # type: ignore[no-untyped-def]
    from tests.review.conftest import make_client

    with make_client(make_settings(), clock, store, review_store) as anonymous:
        response = anonymous.post(f"/api/v1/tickets/{TICKET}/owner-reply", json={})
        assert response.status_code in (401, 403)


def test_the_sign_off_is_not_added_twice(client: TestClient, pending: FakeReviewStore) -> None:
    response = reply(client, {"reply_text": GOOD, "original_viewed": True})
    assert response.status_code == 200, response.text
    assert response.json()["final_text"].count(SIGN_OFF) == 1


def test_a_right_but_uncited_order_fact_is_not_a_warning(
    client: TestClient, pending: FakeReviewStore
) -> None:
    """The owner's reply has no citations, so naming the customer's own order is fine."""
    response = reply(
        client, {"reply_text": "Your order RD-10001 is with me.", "original_viewed": True}
    )
    assert response.status_code == 200, response.text
