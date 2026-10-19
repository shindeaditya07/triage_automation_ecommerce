"""Classification written to a real ticket, and the job handler around it."""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.jobs.worker import PermanentJobError
from services.api.llm.client import CallRecord
from services.api.llm.errors import LLMRequestError
from services.api.models.enums import (
    Flag,
    Intent,
    JobType,
    Sentiment,
    TicketStatus,
    Urgency,
)
from services.api.models.records import Job
from services.api.models.ticket import Classification
from services.api.pipeline.process import make_process_ticket_handler
from services.api.pipeline.store import PipelineStore
from tests.db.jobs_support import T0, make_ticket
from tests.pipeline.test_classify_stage import Scripted, good

pytestmark = pytest.mark.anyio

CALL = CallRecord(
    purpose="classify",
    model="m",
    input_tokens=10,
    output_tokens=5,
    latency_ms=3,
    cost_inr=Decimal("0.5"),
)


def job_for(ticket_id: UUID | None) -> Job:
    return Job(job_id=uuid4(), ticket_id=ticket_id, job_type=JobType.PROCESS_TICKET, run_at=T0)


async def row(engine: AsyncEngine, ticket_id: UUID) -> Any:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT * FROM tickets WHERE ticket_id = :id"), {"id": ticket_id}
            )
        ).one()


async def events(engine: AsyncEngine, ticket_id: UUID) -> list[Any]:
    async with engine.connect() as conn:
        return list(
            (
                await conn.execute(
                    text(
                        "SELECT event_type, payload FROM audit_log WHERE ticket_id = :id"
                        " ORDER BY event_id"
                    ),
                    {"id": ticket_id},
                )
            ).all()
        )


async def test_saving_a_classification_updates_the_ticket_and_audits_it(
    engine: AsyncEngine,
) -> None:
    ticket_id = await make_ticket(engine)
    store = PipelineStore(engine)
    classification = Classification(
        intent=Intent.DAMAGED_ITEM,
        urgency=Urgency.HIGH,
        confidence=0.82,
        sentiment=Sentiment.NEGATIVE,
        flags=[Flag.ANGRY, Flag.COMPENSATION_REQUEST],
    )
    assert await store.save_classification(ticket_id, classification, [CALL], ["high_value"], T0)
    ticket = await row(engine, ticket_id)
    assert ticket.status == "classified"
    assert (ticket.intent, ticket.urgency, ticket.sentiment) == ("damaged_item", "high", "negative")
    assert ticket.confidence == Decimal("0.820")
    assert ticket.flags == ["angry", "compensation_request"]
    [event] = await events(engine, ticket_id)
    assert event.event_type == "ticket.classified"
    assert event.payload["dropped_flags"] == ["high_value"]
    assert event.payload["calls"][0]["cost_inr"] == "0.5"
    assert event.payload["classification"]["intent"] == "damaged_item"


async def test_a_ticket_is_only_advanced_from_new(engine: AsyncEngine) -> None:
    ticket_id = await make_ticket(engine)
    store = PipelineStore(engine)
    assert await store.save_classification(ticket_id, good(), [CALL], [], T0)
    assert not await store.save_classification(ticket_id, good(), [CALL], [], T0)
    assert not await store.escalate(ticket_id, "classification", "x", [], T0)
    assert len(await events(engine, ticket_id)) == 1
    assert (await row(engine, ticket_id)).status == "classified"


async def test_escalating_records_the_reason_and_stage(engine: AsyncEngine) -> None:
    ticket_id = await make_ticket(engine)
    assert await PipelineStore(engine).escalate(ticket_id, "classification", "why", [], T0)
    ticket = await row(engine, ticket_id)
    assert (ticket.status, ticket.escalation_reason) == ("escalated", "why")
    [event] = await events(engine, ticket_id)
    assert event.event_type == "ticket.escalated"
    assert event.payload["stage"] == "classification"


async def test_get_text_hides_a_deleted_ticket(engine: AsyncEngine) -> None:
    ticket_id = await make_ticket(engine)
    store = PipelineStore(engine)
    found = await store.get_text(ticket_id)
    assert found is not None and found.status is TicketStatus.NEW and found.body == "hi"
    assert await store.get_text(uuid4()) is None
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tickets SET deleted_at = now() WHERE ticket_id = :id"), {"id": ticket_id}
        )
    assert await store.get_text(ticket_id) is None


async def test_the_handler_classifies_a_new_ticket_once(engine: AsyncEngine) -> None:
    ticket_id = await make_ticket(engine)
    client = Scripted([good(), good()])
    handle = make_process_ticket_handler(PipelineStore(engine), client, lambda: T0)
    await handle(job_for(ticket_id))
    await handle(job_for(ticket_id))  # a second run changes nothing
    assert len(client.requests) == 1
    assert (await row(engine, ticket_id)).status == "classified"


async def test_the_handler_escalates_when_the_model_fails(engine: AsyncEngine) -> None:
    ticket_id = await make_ticket(engine)
    client = Scripted([LLMRequestError(401)])
    await make_process_ticket_handler(PipelineStore(engine), client, lambda: T0)(job_for(ticket_id))
    ticket = await row(engine, ticket_id)
    assert (ticket.status, ticket.escalation_reason) == ("escalated", "llm_error:LLMRequestError")


async def test_the_handler_gives_up_on_a_missing_ticket(engine: AsyncEngine) -> None:
    handle = make_process_ticket_handler(PipelineStore(engine), Scripted([]), lambda: T0)
    with pytest.raises(PermanentJobError):
        await handle(job_for(None))
    with pytest.raises(PermanentJobError):
        await handle(job_for(uuid4()))
