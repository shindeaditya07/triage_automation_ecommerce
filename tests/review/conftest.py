"""An in-memory review store, so the rules can be tested without a database."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.auth.store import InMemoryAuthStore
from services.api.grounding.edit import EditContext, EditOverride
from services.api.main import create_app
from services.api.models.enums import ReviewStatus, TicketStatus
from services.api.review.service import ReviewService
from services.api.review.store import NotOpen, ReviewState
from tests.auth import conftest as auth
from tests.auth.conftest import login
from tests.grounding.conftest import ORDER, POLICIES, SIGN_OFF, order_cite, reply

# The owner-auth fixtures, under their own names so pytest finds them here.
clock = auth.clock
make_settings = auth.make_settings
owner_hash = auth.owner_hash
store = auth.store

TICKET = uuid4()
GOOD = reply("Your order RD-10001 is with us.")
FLAWED = "We will refund you today."  # G-3 and G-8


class FakeReviewStore:
    def __init__(self, **state: object) -> None:
        self.state = ReviewState(
            ticket_status=TicketStatus.DRAFTED,
            review_status=ReviewStatus.PENDING,
            suggestion_only=False,
            needs_original_viewed=False,
            reply_text=GOOD,
            final_text=None,
            edit=EditContext(
                draft_id=uuid4(),
                customer_email=ORDER["customer_email"],
                subject=None,
                body="Order RD-10001.",
                order=ORDER,
                citations=[order_cite("order_id")],
            ),
        )
        if state:
            self.state = replace(self.state, **state)  # type: ignore[arg-type]
        self.overrides: list[EditOverride] = []
        self.events: list[str] = []
        self.fail_send = False

    async def load(self, ticket_id: UUID) -> ReviewState | None:
        return self.state if ticket_id == TICKET else None

    async def override_rules(self, draft_id: UUID) -> set[str]:
        return set(self.overrides[-1].overridden_rules) if self.overrides else set()

    async def save_edit(self, ticket_id, state, text, actor, now, override) -> None:  # type: ignore[no-untyped-def]
        if not self.state.open:
            raise NotOpen
        self.state = replace(self.state, final_text=text)
        self.events.append("edited")
        if override is not None:
            self.overrides.append(override)

    async def approve(self, ticket_id, state, actor, now: datetime) -> str:  # type: ignore[no-untyped-def]
        if not self.state.open or state.effective_text != self.state.effective_text:
            raise NotOpen
        self.state = replace(
            self.state,
            ticket_status=TicketStatus.APPROVED,
            review_status=ReviewStatus.APPROVED,
            final_text=state.effective_text,
        )
        self.events.append(f"approved by {actor}")
        return state.effective_text

    async def mock_send(self, ticket_id, now) -> bool:  # type: ignore[no-untyped-def]
        if self.fail_send:
            raise RuntimeError("database went away")
        if self.state.ticket_status is not TicketStatus.APPROVED:
            return False
        self.state = replace(self.state, ticket_status=TicketStatus.SENT)
        self.events.append("sent")
        return True

    async def reject(self, ticket_id, state, reason, actor, now) -> None:  # type: ignore[no-untyped-def]
        if not self.state.open:
            raise NotOpen
        self.state = replace(
            self.state,
            ticket_status=TicketStatus.REJECTED,
            review_status=ReviewStatus.REJECTED,
        )
        self.events.append(f"rejected: {reason}")


def make_client(settings, clock, auth_store, review_store):  # type: ignore[no-untyped-def]
    service = ReviewService(review_store, clock, SIGN_OFF, POLICIES)
    app = create_app(
        auth_settings=settings, auth_store=auth_store, clock=clock, review_service=service
    )
    return TestClient(app)


@pytest.fixture
def review_store() -> FakeReviewStore:
    return FakeReviewStore()


@pytest.fixture
def client(make_settings, clock, store: InMemoryAuthStore, review_store):  # type: ignore[no-untyped-def]
    with make_client(make_settings(), clock, store, review_store) as test_client:
        login(test_client)
        yield test_client
