"""The drafting step of the process_ticket handler, on real Postgres and the mock store."""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.llm.errors import LLMInvalidOutputError
from services.api.llm.fake import FakeLLMClient
from services.api.models.enums import Intent
from services.api.pipeline.draft import DraftingConfig
from services.api.pipeline.process import make_process_ticket_handler
from services.api.pipeline.store import PipelineStore
from services.api.policy_source import policies_path
from services.api.retrieval.index import MemoryPolicyIndex
from tests.context.support import store_client
from tests.db.jobs_support import T0
from tests.db.test_order_lookup_step import SCENARIOS, prepare
from tests.db.test_pipeline_store import events, job_for, row
from tests.pipeline.test_classify_stage import good
from tests.pipeline.test_draft_stage import SIGN_OFF, ScriptedDrafter, output

pytestmark = pytest.mark.anyio


class Client(ScriptedDrafter):
    """Classifies with the given classification and drafts from the script."""

    def __init__(self, drafts: list[Any], intent: Intent = Intent.ORDER_STATUS) -> None:
        super().__init__(drafts)
        self._classification = good().model_copy(update={"intent": intent})

    async def classify(self, request: Any) -> Any:
        return await FakeLLMClient.classify(self, request)

    def recorded_classification(self, ticket_id: str) -> Any:
        return self._classification


async def drafting_handler(engine: AsyncEngine, client: Client):  # type: ignore[no-untyped-def]
    index = await MemoryPolicyIndex.build(
        FakeLLMClient(), policies_path().read_text(encoding="utf-8")
    )
    return make_process_ticket_handler(
        PipelineStore(engine),
        client,
        store_client()[0],
        lambda: T0,
        DraftingConfig(index, SIGN_OFF),
    )


async def drafts_of(engine: AsyncEngine, ticket_id: UUID) -> list[Any]:
    async with engine.connect() as conn:
        return list(
            (
                await conn.execute(
                    text("SELECT * FROM drafts WHERE ticket_id = :id"), {"id": ticket_id}
                )
            ).all()
        )


async def ticket_for(engine: AsyncEngine, case: int) -> UUID:
    scenario = SCENARIOS[case]
    return await prepare(engine, scenario.customer_email, f"About {scenario.order_id or 'it'}")


async def test_a_clean_ticket_gets_a_stored_draft_and_becomes_drafted(
    engine: AsyncEngine,
) -> None:
    ticket_id = await ticket_for(engine, 1)
    client = Client([output()])
    await (await drafting_handler(engine, client))(job_for(ticket_id))

    ticket = await row(engine, ticket_id)
    assert ticket.status == "drafted"
    [draft] = await drafts_of(engine, ticket_id)
    assert (draft.attempt, draft.kind, draft.suggestion_only) == (1, "llm", False)
    assert draft.reply_text.endswith(SIGN_OFF)
    assert str(draft.order_snapshot_id) == ticket.order_lookup["snapshot_id"]
    assert draft.retrieved_passage_ids[0] == "TON-9.2"
    assert draft.review_status == "pending" and draft.grounding_result is None
    assert draft.policy_version == "1.1"
    kinds = [e.event_type for e in await events(engine, ticket_id)]
    assert kinds == ["ticket.classified", "ticket.order_lookup", "ticket.drafted"]
    assert len(client.requests) == 1


async def test_the_draft_is_made_once_however_often_the_job_runs(engine: AsyncEngine) -> None:
    ticket_id = await ticket_for(engine, 1)
    client = Client([output(), output()])
    handle = await drafting_handler(engine, client)
    await handle(job_for(ticket_id))
    await handle(job_for(ticket_id))
    assert len(client.requests) == 1
    assert len(await drafts_of(engine, ticket_id)) == 1


async def test_a_late_return_is_drafted_as_suggestion_only(engine: AsyncEngine) -> None:
    ticket_id = await ticket_for(engine, 2)
    client = Client([output()], Intent.RETURN)
    await (await drafting_handler(engine, client))(job_for(ticket_id))

    ticket = await row(engine, ticket_id)
    assert (ticket.status, ticket.escalation_reason) == ("drafted", "outside_policy")
    [draft] = await drafts_of(engine, ticket_id)
    assert draft.suggestion_only is True and "ESC-8.3" in draft.retrieved_passage_ids
    kinds = [e.event_type for e in await events(engine, ticket_id)]
    assert kinds == [
        "ticket.classified",
        "ticket.order_lookup",
        "ticket.suggestion_only",
        "ticket.drafted",
    ]


async def test_a_sender_with_no_order_is_drafted_without_a_snapshot(engine: AsyncEngine) -> None:
    ticket_id = await prepare(engine, "nobody@example.com", "Where is my order?")
    client = Client([output()])
    await (await drafting_handler(engine, client))(job_for(ticket_id))
    [draft] = await drafts_of(engine, ticket_id)
    assert draft.order_snapshot_id is None
    assert client.requests[0].order is None


async def test_a_no_draft_route_never_calls_the_drafter(engine: AsyncEngine) -> None:
    ticket_id = await ticket_for(engine, 19)  # another customer's order
    client = Client([output()])
    await (await drafting_handler(engine, client))(job_for(ticket_id))
    assert (await row(engine, ticket_id)).escalation_reason == "order_mismatch"
    assert client.requests == [] and await drafts_of(engine, ticket_id) == []


async def test_a_drafter_that_keeps_failing_escalates_the_ticket(engine: AsyncEngine) -> None:
    ticket_id = await ticket_for(engine, 1)
    client = Client([LLMInvalidOutputError("a"), LLMInvalidOutputError("b")])
    await (await drafting_handler(engine, client))(job_for(ticket_id))

    ticket = await row(engine, ticket_id)
    assert (ticket.status, ticket.escalation_reason) == ("escalated", "draft_invalid_output")
    assert await drafts_of(engine, ticket_id) == []
    last = (await events(engine, ticket_id))[-1]
    assert last.event_type == "ticket.escalated" and last.payload["stage"] == "draft"
    assert last.payload["calls"][0]["purpose"] == "embed"


async def test_a_job_that_stopped_before_drafting_resumes_there(engine: AsyncEngine) -> None:
    ticket_id = await ticket_for(engine, 1)
    await (await drafting_handler(engine, Client([LLMInvalidOutputError("a")] * 2)))(
        job_for(ticket_id)
    )
    # The ticket was escalated; put it back as if the job had died before drafting.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tickets SET status = 'classified', escalation_reason = NULL"
                " WHERE ticket_id = :id"
            ),
            {"id": ticket_id},
        )
    client = Client([output()])
    await (await drafting_handler(engine, client))(job_for(ticket_id))
    assert (await row(engine, ticket_id)).status == "drafted"
    assert len(await drafts_of(engine, ticket_id)) == 1
