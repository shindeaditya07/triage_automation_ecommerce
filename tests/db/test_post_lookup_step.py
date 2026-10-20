"""The post-lookup routing in the process_ticket handler, on real Postgres and the mock store."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.models.enums import Intent, TicketStatus
from services.api.pipeline.store import PipelineStore
from tests.context.support import NOW, seeded
from tests.db.test_order_lookup_step import SCENARIOS, prepare
from tests.db.test_pipeline_store import events, handler, job_for, row
from tests.pipeline.test_classify_stage import Scripted, good

pytestmark = pytest.mark.anyio


async def run(engine: AsyncEngine, case: int, intent: Intent = Intent.ORDER_STATUS) -> UUID:
    scenario = SCENARIOS[case]
    ticket_id = await prepare(
        engine, scenario.customer_email, f"About {scenario.order_id or 'my order'}"
    )
    classified = good().model_copy(update={"intent": intent})
    await handler(engine, Scripted([classified]))(job_for(ticket_id))
    return ticket_id


async def add_to_thread(engine: AsyncEngine, ticket_id: UUID, *, minutes_before: int) -> None:
    """Another message from the same customer on the ticket's thread."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tickets (ticket_id, external_message_id, thread_id, channel,"
                " received_at, customer_email, body)"
                " SELECT :new, :msg, thread_id, channel, :at, customer_email, 'again'"
                " FROM tickets WHERE ticket_id = :id"
            ),
            {
                "new": uuid4(),
                "msg": f"m-{uuid4()}",
                "at": NOW - timedelta(minutes=minutes_before),
                "id": ticket_id,
            },
        )


async def test_a_missing_order_escalates_with_no_draft(engine: AsyncEngine) -> None:
    ticket_id = await run(engine, 18)
    ticket = await row(engine, ticket_id)
    assert (ticket.status, ticket.escalation_reason) == ("escalated", "order_not_found")
    kinds = [e.event_type for e in await events(engine, ticket_id)]
    assert kinds == ["ticket.classified", "ticket.order_lookup", "ticket.escalated"]
    assert (await events(engine, ticket_id))[-1].payload["stage"] == "post_lookup"


async def test_another_customers_order_escalates_and_stays_flagged(engine: AsyncEngine) -> None:
    ticket_id = await run(engine, 19)
    ticket = await row(engine, ticket_id)
    assert (ticket.status, ticket.escalation_reason) == ("escalated", "order_mismatch")
    assert ticket.flags == ["order_mismatch"]


async def test_a_high_value_order_escalates_and_the_flag_is_set(engine: AsyncEngine) -> None:
    ticket_id = await run(engine, 16)
    ticket = await row(engine, ticket_id)
    assert (ticket.status, ticket.escalation_reason) == ("escalated", "high_value")
    assert ticket.flags == ["high_value"]


async def test_the_third_message_on_a_thread_escalates_as_a_repeat_complaint(
    engine: AsyncEngine,
) -> None:
    scenario = SCENARIOS[1]
    ticket_id = await prepare(engine, scenario.customer_email, scenario.order_id)
    await add_to_thread(engine, ticket_id, minutes_before=60)
    await add_to_thread(engine, ticket_id, minutes_before=30)
    await handler(engine, Scripted([good()]))(job_for(ticket_id))
    ticket = await row(engine, ticket_id)
    assert (ticket.status, ticket.escalation_reason) == ("escalated", "repeat_complaint")
    assert "repeat_complaint" in ticket.flags


async def test_two_messages_on_a_thread_are_not_a_repeat(engine: AsyncEngine) -> None:
    scenario = SCENARIOS[1]
    ticket_id = await prepare(engine, scenario.customer_email, scenario.order_id)
    await add_to_thread(engine, ticket_id, minutes_before=60)
    await handler(engine, Scripted([good()]))(job_for(ticket_id))
    assert (await row(engine, ticket_id)).status == "classified"


async def test_thread_messages_are_counted_up_to_the_ticket_itself(engine: AsyncEngine) -> None:
    ticket_id = await prepare(engine, "a@b.com", "hi")
    store = PipelineStore(engine)
    assert await store.count_thread_messages(ticket_id) == 1
    await add_to_thread(engine, ticket_id, minutes_before=10)
    assert await store.count_thread_messages(ticket_id) == 2
    await add_to_thread(engine, ticket_id, minutes_before=-10)  # a later message does not count
    assert await store.count_thread_messages(ticket_id) == 2


async def test_a_late_return_is_routed_suggestion_only_and_stays_classified(
    engine: AsyncEngine,
) -> None:
    ticket_id = await run(engine, 2, Intent.RETURN)
    ticket = await row(engine, ticket_id)
    assert (ticket.status, ticket.escalation_reason) == ("classified", "outside_policy")
    event = (await events(engine, ticket_id))[-1]
    assert event.event_type == "ticket.suggestion_only"
    assert event.payload["triggers"] == ["return_window_closed"]


async def test_routing_happens_once_however_often_the_job_runs(engine: AsyncEngine) -> None:
    ticket_id = await run(engine, 2, Intent.RETURN)
    for _ in range(2):
        await handler(engine, Scripted([]))(job_for(ticket_id))
    assert len(await events(engine, ticket_id)) == 3

    escalated = await run(engine, 18)
    await handler(engine, Scripted([]))(job_for(escalated))
    assert len(await events(engine, escalated)) == 3


async def test_a_job_that_stopped_after_the_lookup_resumes_at_the_routing(
    engine: AsyncEngine,
) -> None:
    scenario = SCENARIOS[16]
    ticket_id = await prepare(engine, scenario.customer_email, scenario.order_id)
    run_once = handler(engine, Scripted([good()]))
    await run_once(job_for(ticket_id))
    # Undo the routing, as if the job had died between saving the lookup and routing.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tickets SET status = 'classified', escalation_reason = NULL, flags = '{}'"
                " WHERE ticket_id = :id"
            ),
            {"id": ticket_id},
        )
    client = Scripted([])
    await handler(engine, client)(job_for(ticket_id))
    assert client.requests == []
    assert (await row(engine, ticket_id)).escalation_reason == "high_value"


async def test_an_order_that_cannot_be_pinned_down_is_left_for_the_draft_step(
    engine: AsyncEngine,
) -> None:
    ticket_id = await run(engine, 20)  # several orders, none named
    ticket = await row(engine, ticket_id)
    assert (ticket.status, ticket.escalation_reason) == ("classified", None)


async def test_the_recorded_lookup_loads_back_with_its_order(engine: AsyncEngine) -> None:
    scenario = SCENARIOS[1]
    ticket_id = await prepare(engine, scenario.customer_email, scenario.order_id)
    store = PipelineStore(engine)
    assert await store.load_lookup(ticket_id) is None
    await handler(engine, Scripted([good()]))(job_for(ticket_id))
    lookup = await store.load_lookup(ticket_id)
    assert lookup is not None and lookup.order is not None
    assert lookup.order.order_id == scenario.order_id
    assert lookup.history is not None and lookup.history.total_orders == 1


async def test_a_ticket_is_only_marked_suggestion_only_once(engine: AsyncEngine) -> None:
    ticket_id = await run(engine, 1)
    store = PipelineStore(engine)
    ticket = await store.get_text(ticket_id)
    assert ticket is not None and ticket.classification is not None
    flags = list(ticket.classification.flags)
    assert await store.mark_suggestion_only(ticket_id, "outside_policy", ["x"], flags, NOW)
    assert not await store.mark_suggestion_only(ticket_id, "outside_policy", ["x"], flags, NOW)
    after = await store.get_text(ticket_id)
    assert after is not None and after.status is TicketStatus.CLASSIFIED


def test_the_seeded_cases_used_here_exist() -> None:
    assert {1, 2, 16, 18, 19, 20} <= {s.case for s in seeded().scenarios}
