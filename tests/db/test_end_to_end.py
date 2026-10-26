"""A ticket from the customer's message to a recorded send, on real Postgres: the pipeline drafts
it, the owner edits and approves it through the review service, and the audit log tells the whole
story (build plan 5.3, with the fake model and the mock store)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.auth.service import Owner
from services.api.policy_source import loaded_passages
from services.api.review.models import ApproveRequest, SaveEditRequest
from services.api.review.service import ReviewService
from services.api.review.store import ReviewStore
from tests.db.test_draft_step import Client, drafting_handler, drafts_of, ticket_for
from tests.db.test_pipeline_store import job_for
from tests.pipeline.test_draft_stage import SIGN_OFF, output

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)


async def trail(engine: AsyncEngine, ticket_id) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT event_type, actor FROM audit_log WHERE ticket_id = :t ORDER BY event_id"),
            {"t": ticket_id},
        )
        return [(r.event_type, r.actor) for r in rows]


async def test_a_ticket_goes_from_message_to_recorded_send(engine: AsyncEngine) -> None:
    ticket_id = await ticket_for(engine, 1)
    await (await drafting_handler(engine, Client([output()])))(job_for(ticket_id))

    review = ReviewService(ReviewStore(engine), lambda: NOW, SIGN_OFF, loaded_passages())
    owner = Owner(sid=uuid4(), email="owner@example.com")
    [draft] = await drafts_of(engine, ticket_id)

    edited = draft.reply_text.replace("Hello", "Hi")
    assert edited != draft.reply_text
    await review.save_edit(ticket_id, SaveEditRequest(reply_text=edited), owner)
    outcome = await review.approve(ticket_id, ApproveRequest(), owner)

    assert outcome.ticket_status.value == "sent"
    assert outcome.final_text == edited
    [after] = await drafts_of(engine, ticket_id)
    assert after.reply_text == draft.reply_text  # the model's text is kept
    assert (after.final_text, after.review_status, after.reviewed_by) == (
        edited,
        "approved",
        "owner@example.com",
    )
    assert after.sent_at == NOW
    assert await trail(engine, ticket_id) == [
        ("ticket.classified", "system"),
        ("ticket.order_lookup", "system"),
        ("ticket.drafted", "system"),
        ("draft.edited", "owner@example.com"),
        ("ticket.approved", "owner@example.com"),
        ("ticket.mock_sent", "system"),
    ]
