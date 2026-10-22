"""The owner's queue and ticket screen, read from real drafted tickets."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.models.enums import TicketStatus
from services.api.pipeline.store import PipelineStore
from services.api.policy_source import loaded_passages
from services.api.tickets.store import TicketFilter, TicketReadStore
from tests.db.test_draft_step import Client, drafting_handler, ticket_for
from tests.db.test_pipeline_store import job_for
from tests.pipeline.test_draft_stage import output

pytestmark = pytest.mark.anyio


def reader(engine: AsyncEngine) -> TicketReadStore:
    return TicketReadStore(engine, loaded_passages())


async def drafted(engine: AsyncEngine, case: int):  # type: ignore[no-untyped-def]
    ticket_id = await ticket_for(engine, case)
    await (await drafting_handler(engine, Client([output()])))(job_for(ticket_id))
    return ticket_id


async def set_urgency(engine: AsyncEngine, ticket_id, urgency: str) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tickets SET urgency = :u WHERE ticket_id = :t"),
            {"u": urgency, "t": ticket_id},
        )


async def everything(store: TicketReadStore, flt: TicketFilter | None = None):  # type: ignore[no-untyped-def]
    """Every ticket the filter matches (the database is shared by the tests in this module)."""
    page = await store.list_tickets(flt or TicketFilter(), 100, 0)
    return page.items


async def test_the_queue_is_most_urgent_first_then_oldest(engine: AsyncEngine) -> None:
    low, critical, normal = [await drafted(engine, n) for n in (1, 2, 3)]
    for ticket_id, urgency in ((low, "low"), (critical, "critical"), (normal, "normal")):
        await set_urgency(engine, ticket_id, urgency)
    items = await everything(reader(engine))
    mine = [i for i in items if i.ticket_id in {low, critical, normal}]
    assert [i.ticket_id for i in mine] == [critical, normal, low]
    assert all(i.has_draft and not i.used_fallback_template for i in mine)
    ranks = [
        {"critical": 0, "high": 1, "normal": 2, "low": 3}.get(i.urgency or "", 4) for i in items
    ]
    assert ranks == sorted(ranks)


async def test_filters_and_paging(engine: AsyncEngine) -> None:
    ids = {await drafted(engine, n) for n in (1, 2, 3)}
    store = reader(engine)
    drafted_only = await everything(store, TicketFilter((TicketStatus.DRAFTED,)))
    assert ids <= {i.ticket_id for i in drafted_only}
    assert all(i.status is TicketStatus.DRAFTED for i in drafted_only)
    assert await everything(store, TicketFilter((TicketStatus.REJECTED,))) == []
    everyone = await store.list_tickets(TicketFilter(), 100, 0)
    first = await store.list_tickets(TicketFilter(), 2, 0)
    second = await store.list_tickets(TicketFilter(), 2, 2)
    assert first.total == second.total == everyone.total >= 3
    assert [i.ticket_id for i in first.items + second.items] == [
        i.ticket_id for i in everyone.items[:4]
    ]
    intents = {i.intent for i in everyone.items if i.intent}
    for intent in intents:
        matched = await everything(store, TicketFilter(intent=intent))
        assert matched and all(i.intent == intent for i in matched)


async def test_a_deleted_ticket_is_not_shown(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine, 1)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tickets SET deleted_at = now() WHERE ticket_id = :t"), {"t": ticket_id}
        )
    store = reader(engine)
    assert ticket_id not in {i.ticket_id for i in await everything(store)}
    assert await store.get_ticket(ticket_id) is None


async def test_the_detail_has_the_draft_its_order_and_cited_policy(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine, 1)
    detail = await reader(engine).get_ticket(ticket_id)
    assert detail is not None and detail.draft is not None
    assert detail.status is TicketStatus.DRAFTED
    assert detail.order is not None and detail.order["order_id"].startswith("RD-")
    assert detail.draft.reply_text and detail.draft.grounding_result is not None
    assert detail.body and detail.customer_email
    assert not detail.needs_original_viewed


async def test_an_unknown_ticket_is_none(engine: AsyncEngine) -> None:
    assert await reader(engine).get_ticket(uuid4()) is None


async def test_a_ticket_with_no_draft_has_none(engine: AsyncEngine) -> None:
    ticket_id = await ticket_for(engine, 1)
    detail = await reader(engine).get_ticket(ticket_id)
    assert detail is not None and detail.draft is None and detail.order is None
    mine = [i for i in await everything(reader(engine)) if i.ticket_id == ticket_id]
    assert [i.has_draft for i in mine] == [False]


async def test_an_escalated_ticket_must_have_its_original_viewed(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine, 1)
    await PipelineStore(engine).escalate(
        ticket_id, "draft", "draft_failed", [], datetime.now(UTC), from_status=TicketStatus.DRAFTED
    )
    detail = await reader(engine).get_ticket(ticket_id)
    assert detail is not None and detail.needs_original_viewed
