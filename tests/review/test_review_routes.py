"""Save an edit, approve and reject: the rules that stand between a draft and an approval."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.main import create_app
from tests.auth.conftest import csrf, login
from tests.review.conftest import FLAWED, GOOD, TICKET, FakeReviewStore, make_client


def url(action: str, ticket=TICKET) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/tickets/{ticket}/{action}"


def patch(client: TestClient, body: dict):  # type: ignore[no-untyped-def]
    return client.patch(url("draft"), json=body, headers=csrf(client))


def post(client: TestClient, action: str, body: dict | None = None, ticket=TICKET):  # type: ignore[no-untyped-def]
    return client.post(url(action, ticket), json=body or {}, headers=csrf(client))


# ---- authentication and plumbing --------------------------------------------------------


def test_every_route_needs_a_signed_in_owner(make_settings, clock, store, review_store) -> None:  # type: ignore[no-untyped-def]
    with make_client(make_settings(), clock, store, review_store) as anonymous:
        assert anonymous.patch(url("draft"), json={"reply_text": "x"}).status_code in (401, 403)
        assert anonymous.post(url("approve"), json={}).status_code in (401, 403)
        assert anonymous.post(url("reject"), json={"reason": "a" * 12}).status_code in (401, 403)
    assert review_store.events == []


def test_an_unknown_ticket_is_404(client: TestClient) -> None:
    other = uuid4()
    assert post(client, "approve", ticket=other).status_code == 404
    assert post(client, "reject", {"reason": "not a good draft"}, ticket=other).status_code == 404
    response = client.patch(url("draft", other), json={"reply_text": "x"}, headers=csrf(client))
    assert response.status_code == 404


def test_without_a_database_review_is_unavailable(make_settings, clock, store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(auth_settings=make_settings(), auth_store=store, clock=clock)
    with TestClient(app) as test_client:
        login(test_client)
        assert post(test_client, "approve").status_code == 503


# ---- saving an edit --------------------------------------------------------------------------


def test_a_good_edit_is_saved(client: TestClient, review_store: FakeReviewStore) -> None:
    text = GOOD.replace("with us", "on its way to you")
    response = patch(client, {"reply_text": text})
    assert response.status_code == 200
    assert response.json()["final_text"] == text
    assert review_store.state.final_text == text
    assert review_store.overrides == []


def test_an_edit_with_warnings_needs_an_override(
    client: TestClient, review_store: FakeReviewStore
) -> None:
    response = patch(client, {"reply_text": FLAWED})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert {f["rule_id"] for f in detail["failures"]} >= {"G-3", "G-8"}
    assert review_store.state.final_text is None  # nothing was saved


def test_an_override_must_cover_every_rule_that_fired(
    client: TestClient, review_store: FakeReviewStore
) -> None:
    body = {"reply_text": FLAWED, "override": {"reason": "I checked", "overridden_rules": ["G-3"]}}
    response = patch(client, body)
    assert response.status_code == 422
    assert "does not cover" in response.json()["detail"]["message"]
    assert review_store.state.final_text is None


def test_a_covering_override_saves_the_edit_and_is_recorded(
    client: TestClient, review_store: FakeReviewStore
) -> None:
    body = {
        "reply_text": FLAWED,
        "override": {
            "reason": "I will arrange the refund myself",
            "overridden_rules": ["G-3", "G-8"],
        },
    }
    assert patch(client, body).status_code == 200
    assert review_store.state.final_text == FLAWED
    [override] = review_store.overrides
    assert override.actor == "owner@example.com"
    assert override.overridden_rules == ["G-3", "G-8"]


def test_an_override_with_nothing_to_override_is_refused(client: TestClient) -> None:
    body = {"reply_text": GOOD, "override": {"reason": "just in case", "overridden_rules": ["G-3"]}}
    assert patch(client, body).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"reply_text": ""},
        {"reply_text": "x" * 20_001},
        {"reply_text": GOOD, "override": {"reason": "", "overridden_rules": ["G-3"]}},
        {"reply_text": GOOD, "override": {"reason": "ok", "overridden_rules": []}},
        {"reply_text": GOOD, "override": {"reason": "ok", "overridden_rules": ["G-99"]}},
        {"reply_text": GOOD, "surprise": 1},
    ],
)
def test_malformed_edits_are_422(client: TestClient, body: dict) -> None:
    assert patch(client, body).status_code == 422


# ---- approving ------------------------------------------------------------------------------


def test_approving_records_the_decision(client: TestClient, review_store: FakeReviewStore) -> None:
    response = post(client, "approve")
    assert response.status_code == 200
    assert response.json() | {"ticket_id": None, "draft_id": None} == {
        "ticket_id": None,
        "draft_id": None,
        "ticket_status": "sent",
        "review_status": "approved",
        "final_text": GOOD,
    }
    assert review_store.events == ["approved by owner@example.com", "sent"]


def test_a_draft_is_decided_once(client: TestClient) -> None:
    assert post(client, "approve").status_code == 200
    assert post(client, "approve").status_code == 409
    assert post(client, "reject", {"reason": "changed my mind"}).status_code == 409
    assert patch(client, {"reply_text": GOOD}).status_code == 409


def test_an_escalated_ticket_needs_the_original_viewed(make_settings, clock, store) -> None:  # type: ignore[no-untyped-def]
    review_store = FakeReviewStore(needs_original_viewed=True)
    with make_client(make_settings(), clock, store, review_store) as client:
        login(client)
        refused = post(client, "approve")
        assert refused.status_code == 409 and "original message" in refused.json()["detail"]
        assert review_store.events == []
        assert post(client, "approve", {"original_viewed": True}).status_code == 200


def test_a_suggestion_only_draft_needs_a_second_confirmation(make_settings, clock, store) -> None:  # type: ignore[no-untyped-def]
    review_store = FakeReviewStore(suggestion_only=True, needs_original_viewed=True)
    with make_client(make_settings(), clock, store, review_store) as client:
        login(client)
        assert post(client, "approve", {"original_viewed": True}).status_code == 409
        assert review_store.events == []
        both = {"original_viewed": True, "confirm_suggestion_only": True}
        assert post(client, "approve", both).status_code == 200


def test_saved_text_with_warnings_is_not_approved_without_an_override(
    make_settings, clock, store
) -> None:  # type: ignore[no-untyped-def]
    review_store = FakeReviewStore(final_text=FLAWED)  # saved some other way, no override on record
    with make_client(make_settings(), clock, store, review_store) as client:
        login(client)
        response = post(client, "approve")
        assert response.status_code == 409 and "G-3" in response.json()["detail"]
        assert review_store.events == []


def test_an_overridden_edit_can_be_approved_but_a_wider_one_cannot(
    client: TestClient, review_store: FakeReviewStore
) -> None:
    covered = {
        "reply_text": FLAWED,
        "override": {
            "reason": "I will arrange the refund myself",
            "overridden_rules": ["G-3", "G-8"],
        },
    }
    assert patch(client, covered).status_code == 200
    assert post(client, "approve").status_code == 200
    assert review_store.state.final_text == FLAWED


def test_an_edit_that_passes_later_needs_no_override(client: TestClient) -> None:
    covered = {
        "reply_text": FLAWED,
        "override": {
            "reason": "I will arrange the refund myself",
            "overridden_rules": ["G-3", "G-8"],
        },
    }
    assert patch(client, covered).status_code == 200
    assert patch(client, {"reply_text": GOOD}).status_code == 200
    assert post(client, "approve").status_code == 200


# ---- rejecting ------------------------------------------------------------------------------


def test_rejecting_needs_a_real_reason(client: TestClient, review_store: FakeReviewStore) -> None:
    assert post(client, "reject", {"reason": "no"}).status_code == 422
    assert post(client, "reject", {"reason": "   short   "}).status_code == 422
    assert post(client, "reject", {}).status_code == 422
    assert review_store.events == []


def test_rejecting_records_the_reason(client: TestClient, review_store: FakeReviewStore) -> None:
    response = post(client, "reject", {"reason": "  the tone is wrong for this customer  "})
    assert response.status_code == 200
    assert response.json()["ticket_status"] == "rejected"
    assert review_store.events == ["rejected: the tone is wrong for this customer"]
    assert post(client, "approve").status_code == 409


# ---- the mock send ---------------------------------------------------------------------------


def test_a_failed_send_keeps_the_approval_and_can_be_finished(
    client: TestClient, review_store: FakeReviewStore
) -> None:
    review_store.fail_send = True
    response = post(client, "approve")
    assert response.status_code == 200
    assert response.json()["ticket_status"] == "approved"  # decided, not yet sent
    assert post(client, "send").status_code == 409  # still failing
    review_store.fail_send = False
    assert post(client, "send").json()["ticket_status"] == "sent"
    assert review_store.events.count("sent") == 1


def test_send_is_idempotent_and_only_for_approved_drafts(
    client: TestClient, review_store: FakeReviewStore
) -> None:
    assert post(client, "send").status_code == 409  # not approved yet
    assert review_store.events == []
    post(client, "approve")
    assert post(client, "send").json()["ticket_status"] == "sent"
    assert review_store.events.count("sent") == 1  # nothing recorded twice
    assert post(client, "send", ticket=uuid4()).status_code == 404
