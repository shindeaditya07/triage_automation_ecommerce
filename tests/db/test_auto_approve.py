"""Auto-approval through the real pipeline: a confident draft is approved and mock-sent by the
system with its audit trail; a less sure one waits for the owner."""

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.llm.fake import FakeLLMClient
from services.api.pipeline.draft import DraftingConfig
from services.api.pipeline.process import make_process_ticket_handler
from services.api.pipeline.store import PipelineStore
from services.api.policy_source import loaded_passages, policies_path
from services.api.retrieval.index import MemoryPolicyIndex
from services.api.review.service import ReviewService
from services.api.review.store import ReviewStore
from tests.context.support import store_client
from tests.db.jobs_support import T0
from tests.db.test_draft_step import Client, ticket_for
from tests.db.test_pipeline_store import job_for
from tests.pipeline.test_draft_stage import SIGN_OFF, output

pytestmark = pytest.mark.anyio


async def run(engine: AsyncEngine, confidence: float, threshold: float = 0.90) -> UUID:
    ticket_id = await ticket_for(engine, 1)
    index = await MemoryPolicyIndex.build(
        FakeLLMClient(), policies_path().read_text(encoding="utf-8")
    )
    reviewer = ReviewService(ReviewStore(engine), lambda: T0, SIGN_OFF, loaded_passages())

    async def after_draft(ticket_id: UUID) -> object:
        return await reviewer.auto_approve(ticket_id, threshold)

    handler = make_process_ticket_handler(
        PipelineStore(engine),
        Client([output().model_copy(update={"confidence": confidence})]),
        store_client()[0],
        lambda: T0,
        DraftingConfig(index, SIGN_OFF),
        after_draft,
    )
    await handler(job_for(ticket_id))
    return ticket_id


async def state_of(engine: AsyncEngine, ticket_id: UUID):  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT t.status, d.review_status, d.reviewed_by, d.sent_at FROM tickets t "
                    "JOIN drafts d USING (ticket_id) WHERE t.ticket_id = :t"
                ),
                {"t": ticket_id},
            )
        ).one()
        events = (
            await conn.execute(
                text(
                    "SELECT event_type, actor, payload FROM audit_log WHERE ticket_id = :t "
                    "AND event_type IN ('ticket.approved', 'ticket.auto_approved', "
                    "'ticket.mock_sent') ORDER BY event_id"
                ),
                {"t": ticket_id},
            )
        ).all()
    return row, [(e.event_type, e.actor, e.payload) for e in events]


async def test_a_confident_draft_is_approved_and_sent_with_no_owner(engine: AsyncEngine) -> None:
    ticket_id = await run(engine, confidence=0.95)
    row, events = await state_of(engine, ticket_id)
    assert (row.status, row.review_status) == ("sent", "approved")
    assert row.reviewed_by == "system:auto-approve" and row.sent_at is not None
    assert [(t, a) for t, a, _ in events] == [
        ("ticket.auto_approved", "system:auto-approve"),
        ("ticket.mock_sent", "system"),
    ]
    payload = events[0][2]
    assert payload["threshold"] == 0.9
    assert payload["draft_confidence"] == 0.95
    assert payload["confidence"] == min(payload["classifier_confidence"], 0.95)
    assert payload["edited"] is False


async def test_a_less_sure_draft_waits_for_the_owner(engine: AsyncEngine) -> None:
    ticket_id = await run(engine, confidence=0.6)
    row, events = await state_of(engine, ticket_id)
    assert (row.status, row.review_status, row.reviewed_by) == ("drafted", "pending", None)
    assert events == []


async def test_a_higher_threshold_holds_back_what_a_lower_one_lets_through(
    engine: AsyncEngine,
) -> None:
    ticket_id = await run(engine, confidence=0.95, threshold=0.99)
    row, _ = await state_of(engine, ticket_id)
    assert row.status == "drafted"
