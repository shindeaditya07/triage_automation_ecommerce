"""The order lookup step of the process_ticket handler, on real Postgres and the mock store."""

from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.context.client import StoreUnavailable
from services.api.jobs.worker import PermanentJobError
from tests.context.support import NOW, seeded, store_client
from tests.db.jobs_support import make_ticket
from tests.db.test_pipeline_store import events, handler, job_for, row
from tests.pipeline.test_classify_stage import Scripted, good

pytestmark = pytest.mark.anyio

SCENARIOS = {s.case: s for s in seeded().scenarios}


async def prepare(
    engine: AsyncEngine, email: str, body: str = "Where is my order?", hint: str | None = None
) -> UUID:
    ticket_id = await make_ticket(engine)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tickets SET customer_email = :email, body = :body, order_id_hint = :hint,"
                " received_at = :at WHERE ticket_id = :id"
            ),
            {"email": email, "body": body, "hint": hint, "at": NOW, "id": ticket_id},
        )
    return ticket_id


async def snapshots(engine: AsyncEngine, ticket_id: UUID) -> list[Any]:
    async with engine.connect() as conn:
        return list(
            (
                await conn.execute(
                    text("SELECT * FROM order_snapshots WHERE ticket_id = :id"), {"id": ticket_id}
                )
            ).all()
        )


async def test_a_found_order_is_snapshotted_and_the_lookup_recorded(engine: AsyncEngine) -> None:
    scenario = SCENARIOS[8]
    ticket_id = await prepare(engine, scenario.customer_email, f"Order {scenario.order_id} broke")
    await handler(engine, Scripted([good()]))(job_for(ticket_id))

    ticket = await row(engine, ticket_id)
    assert ticket.status == "classified"
    [snapshot] = await snapshots(engine, ticket_id)
    assert snapshot.order_id == scenario.order_id
    assert snapshot.data["order_id"] == scenario.order_id
    assert snapshot.data["status"] == "delivered"
    assert ticket.order_lookup["result"] == "found"
    assert ticket.order_lookup["snapshot_id"] == str(snapshot.snapshot_id)
    assert ticket.order_lookup["history"]["total_orders"] == 1
    kinds = [e.event_type for e in await events(engine, ticket_id)]
    assert kinds == ["ticket.classified", "ticket.order_lookup"]


async def test_a_mismatch_sets_the_flag_and_keeps_no_order_data(engine: AsyncEngine) -> None:
    scenario = SCENARIOS[19]
    ticket_id = await prepare(engine, scenario.customer_email, f"Refund {scenario.order_id}")
    await handler(engine, Scripted([good()]))(job_for(ticket_id))

    ticket = await row(engine, ticket_id)
    assert ticket.flags == ["order_mismatch"]
    assert ticket.order_lookup["result"] == "mismatch"
    assert ticket.order_lookup["snapshot_id"] is None
    assert await snapshots(engine, ticket_id) == []
    # Nothing about the victim's order is in the audit trail either.
    payloads = " ".join(str(e.payload) for e in await events(engine, ticket_id))
    assert SCENARIOS[19].customer_email not in payloads.replace(scenario.customer_email, "")


async def test_a_missing_order_and_an_ambiguous_sender_are_recorded_without_flags(
    engine: AsyncEngine,
) -> None:
    ghost = SCENARIOS[18]
    gone = await prepare(engine, ghost.customer_email, f"Where is {ghost.order_id}?")
    many = await prepare(engine, SCENARIOS[20].customer_email)
    nobody = await prepare(engine, "nobody@example.com")
    for ticket_id in (gone, many, nobody):
        await handler(engine, Scripted([good()]))(job_for(ticket_id))
    results = [(await row(engine, t)).order_lookup["result"] for t in (gone, many, nobody)]
    assert results == ["not_found", "ambiguous", "no_order"]
    for ticket_id in (gone, many, nobody):
        assert (await row(engine, ticket_id)).flags == []
        assert await snapshots(engine, ticket_id) == []


async def test_the_form_hint_is_used(engine: AsyncEngine) -> None:
    scenario = SCENARIOS[1]
    ticket_id = await prepare(engine, scenario.customer_email, hint=scenario.order_id)
    await handler(engine, Scripted([good()]))(job_for(ticket_id))
    assert (await row(engine, ticket_id)).order_lookup["result"] == "found"


async def test_the_lookup_runs_once(engine: AsyncEngine) -> None:
    scenario = SCENARIOS[1]
    ticket_id = await prepare(engine, scenario.customer_email, scenario.order_id)
    run = handler(engine, Scripted([good()]))
    await run(job_for(ticket_id))
    await run(job_for(ticket_id))
    assert len(await snapshots(engine, ticket_id)) == 1
    assert len(await events(engine, ticket_id)) == 2


async def test_an_escalated_ticket_is_not_looked_up(engine: AsyncEngine) -> None:
    scenario = SCENARIOS[1]
    body = f"{scenario.order_id}: my lawyer will send a legal notice"
    ticket_id = await prepare(engine, scenario.customer_email, body)
    await handler(engine, Scripted([good()]))(job_for(ticket_id))
    ticket = await row(engine, ticket_id)
    assert ticket.status == "escalated" and ticket.order_lookup is None
    assert await snapshots(engine, ticket_id) == []


async def test_an_unreachable_store_fails_the_job_so_it_is_retried(engine: AsyncEngine) -> None:
    ticket_id = await prepare(engine, SCENARIOS[1].customer_email)

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    orders, http = store_client(down)
    async with http:
        with pytest.raises(StoreUnavailable):
            await handler(engine, Scripted([good()]), orders)(job_for(ticket_id))
    ticket = await row(engine, ticket_id)
    assert ticket.status == "classified" and ticket.order_lookup is None

    # When the store is back, the next try picks up at the lookup without classifying again.
    client = Scripted([])
    await handler(engine, client)(job_for(ticket_id))
    assert client.requests == []
    assert (await row(engine, ticket_id)).order_lookup["result"] == "found"


async def test_a_store_reply_outside_the_contract_is_a_permanent_failure(
    engine: AsyncEngine,
) -> None:
    ticket_id = await prepare(engine, SCENARIOS[1].customer_email)

    def odd(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    orders, http = store_client(odd)
    async with http:
        with pytest.raises(PermanentJobError):
            await handler(engine, Scripted([good()]), orders)(job_for(ticket_id))
