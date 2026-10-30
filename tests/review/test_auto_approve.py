"""A confident, clean draft is approved and mock-sent with no owner; anything else waits."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from services.api.models.enums import DraftKind, TicketStatus
from services.api.review.auto import AUTO_APPROVER, auto_approve_block, confidence_of
from services.api.review.service import ReviewService
from tests.review.conftest import FLAWED, POLICIES, SIGN_OFF, TICKET, FakeReviewStore

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
SURE = {"classifier_confidence": 0.95, "draft_confidence": 0.93}


def service(store: FakeReviewStore) -> ReviewService:
    return ReviewService(store, lambda: NOW, SIGN_OFF, POLICIES)


def test_confidence_is_the_lower_of_the_two() -> None:
    state = FakeReviewStore(**SURE).state
    assert confidence_of(state) == 0.93
    assert confidence_of(replace(state, classifier_confidence=None)) is None


def test_a_confident_clean_draft_may_go() -> None:
    assert auto_approve_block(FakeReviewStore(**SURE).state, 0.90) is None


def test_the_threshold_is_inclusive() -> None:
    state = FakeReviewStore(classifier_confidence=0.9, draft_confidence=0.9).state
    assert auto_approve_block(state, 0.90) is None
    assert auto_approve_block(state, 0.91) is not None


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"draft_confidence": 0.89}, "below"),
        ({"classifier_confidence": 0.89}, "below"),
        ({"classifier_confidence": None}, "no classifier confidence"),
        ({"suggestion_only": True}, "suggestion-only"),
        ({"draft_kind": DraftKind.FALLBACK_TEMPLATE}, "not one the model wrote"),
        ({"escalation_reason": "legal_threat"}, "escalated"),
        ({"flags": ("prompt_injection_suspected",)}, "injection"),
        ({"final_text": "The owner's own words"}, "already edited"),
        ({"ticket_status": TicketStatus.APPROVED}, "not waiting"),
    ],
)
def test_anything_that_asks_for_a_person_waits(change: dict[str, object], why: str) -> None:
    state = replace(FakeReviewStore(**SURE).state, **change)  # type: ignore[arg-type]
    assert why in (auto_approve_block(state, 0.90) or "")


async def test_a_clean_draft_is_approved_and_sent_by_the_system() -> None:
    store = FakeReviewStore(**SURE)
    outcome = await service(store).auto_approve(TICKET, 0.90)
    assert outcome is not None
    assert outcome.ticket_status is TicketStatus.SENT
    assert store.events == [f"approved by {AUTO_APPROVER}", "sent"]


async def test_a_low_confidence_draft_is_left_for_the_owner() -> None:
    store = FakeReviewStore(classifier_confidence=0.95, draft_confidence=0.7)
    assert await service(store).auto_approve(TICKET, 0.90) is None
    assert store.events == []
    assert store.state.ticket_status is TicketStatus.DRAFTED


async def test_text_with_grounding_warnings_is_left_for_the_owner() -> None:
    store = FakeReviewStore(reply_text=FLAWED, **SURE)
    assert await service(store).auto_approve(TICKET, 0.90) is None
    assert store.events == []


async def test_a_missing_ticket_is_left_alone() -> None:
    assert await service(FakeReviewStore(**SURE)).auto_approve(uuid4(), 0.90) is None
