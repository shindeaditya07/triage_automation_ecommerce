"""Every required edge case (spec section 17) through the whole pipeline on real Postgres.

Each ticket is ingested the way the mail webhook does it, processed by the real handler (classify,
escalation checks, order lookup against the seeded mock store, retrieval, drafting, grounding) with
the fake model, and then judged on where it lands: status, escalation reason, whether the owner may
one-click approve it, and what the audit log says. Build plan 5.3.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.intake.service import IntakeService
from services.api.intake.store import PostgresTicketStore
from services.api.jobs.store import JobStore
from services.api.models.enums import Intent
from services.api.models.ticket import Attachment, Customer, TicketCreateRequest
from tests.context.support import NOW
from tests.db.jobs_support import FakeClock
from tests.db.test_draft_step import Client, drafting_handler, drafts_of
from tests.db.test_order_lookup_step import SCENARIOS
from tests.db.test_pipeline_store import events, job_for, row
from tests.pipeline.test_classify_stage import good
from tests.pipeline.test_draft_stage import output

pytestmark = pytest.mark.anyio


@dataclass(frozen=True)
class Expect:
    intent: Intent
    status: str = "drafted"
    reason: str | None = None
    suggestion_only: bool = False
    lookup: str | None = "found"
    flags: tuple[str, ...] = ()


EXPECTED: dict[int, Expect] = {
    1: Expect(Intent.RETURN),
    2: Expect(Intent.RETURN, reason="outside_policy", suggestion_only=True),
    3: Expect(Intent.ORDER_STATUS),
    4: Expect(Intent.ORDER_STATUS),
    5: Expect(Intent.ORDER_STATUS),
    6: Expect(Intent.CANCELLATION),
    7: Expect(Intent.CANCELLATION),
    8: Expect(Intent.DAMAGED_ITEM),
    9: Expect(Intent.DAMAGED_ITEM),
    10: Expect(Intent.DAMAGED_ITEM, reason="outside_policy", suggestion_only=True),
    11: Expect(Intent.RETURN, reason="outside_policy", suggestion_only=True),
    12: Expect(Intent.RETURN),
    13: Expect(Intent.REFUND),
    14: Expect(Intent.REFUND),
    15: Expect(Intent.REFUND),
    16: Expect(Intent.REFUND, "escalated", "high_value", flags=("high_value",)),
    17: Expect(Intent.ADDRESS_CHANGE),
    18: Expect(Intent.ORDER_STATUS, "escalated", "order_not_found", lookup="not_found"),
    19: Expect(
        Intent.REFUND, "escalated", "order_mismatch", lookup="mismatch", flags=("order_mismatch",)
    ),
    20: Expect(Intent.ORDER_STATUS, lookup="ambiguous"),
    21: Expect(Intent.ORDER_STATUS, "escalated", "repeat_complaint", flags=("repeat_complaint",)),
    22: Expect(Intent.ORDER_STATUS),
    23: Expect(Intent.ORDER_STATUS),
    24: Expect(Intent.ORDER_STATUS),
    25: Expect(Intent.REFUND),
    26: Expect(Intent.ORDER_STATUS),
    27: Expect(Intent.ORDER_STATUS),
    28: Expect(Intent.DAMAGED_ITEM),
    29: Expect(
        Intent.OTHER, "escalated", "low_confidence,intent_other", lookup=None
    ),  # before any lookup
    30: Expect(Intent.ORDER_STATUS),
    31: Expect(Intent.WARRANTY),
}


def request(case: int, name: str, **overrides: Any) -> TicketCreateRequest:
    scenario = SCENARIOS[case]
    values: dict[str, Any] = {
        "external_message_id": f"<case-{case}-{name}-{uuid4().hex}@mail>",
        "channel": "email",
        "customer": Customer(email=scenario.customer_email),
        "subject": "Help with my order",
        "body": f"About {scenario.order_id or 'my order'}",
    } | overrides
    return TicketCreateRequest(**values)


async def ingest(engine: AsyncEngine, req: TicketCreateRequest) -> Any:
    service = IntakeService(PostgresTicketStore(engine, JobStore(engine)), FakeClock())
    return await service.ingest(req)


async def set_received(engine: AsyncEngine, ticket_id: UUID, at: datetime) -> None:
    """The seed's dates are relative to the frozen clock, so tickets are received on it too."""
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tickets SET received_at = :at WHERE ticket_id = :id"),
            {"at": at, "id": ticket_id},
        )


async def prior_thread(engine: AsyncEngine, case: int, messages: int) -> str:
    """`messages` earlier messages on one thread; returns the id of the last, to reply to."""
    last = None
    for n in range(messages):
        req = request(case, f"earlier-{n}", in_reply_to=last)
        earlier = await ingest(engine, req)
        await set_received(engine, earlier.ticket_id, NOW - timedelta(hours=messages - n))
        last = req.external_message_id
    assert last is not None
    return last


async def process(engine: AsyncEngine, case: int, expect: Expect) -> tuple[UUID, Any, Client]:
    """Ingest the case's message the way the case needs it, run the handler, return the result."""
    overrides: dict[str, Any] = {}
    if case == 21:  # two earlier complaints, so this is the third message on the thread
        overrides["in_reply_to"] = await prior_thread(engine, case, 2)
    if case == 23:
        overrides["in_reply_to"] = await prior_thread(engine, case, 1)
    if case == 9:
        overrides["body"] = "My lamp arrived cracked. " + str(SCENARIOS[case].order_id)
    if case == 28:
        first = request(case, "damage", body=f"{SCENARIOS[case].order_id} arrived cracked")
        await set_received(
            engine, (await ingest(engine, first)).ticket_id, NOW - timedelta(hours=1)
        )
        overrides["in_reply_to"] = first.external_message_id
        overrides["body"] = "Here is the photo"
        overrides["attachments"] = [
            Attachment(filename="crack.jpg", content_type="image/jpeg", size_bytes=1000)
        ]
    if case == 29:
        overrides["body"] = "मेरा ऑर्डर कहाँ है? " + str(SCENARIOS[case].order_id)
    if case == 30:
        overrides["body"] = f"Where is {SCENARIOS[case].order_id}, and can I also return it?"
    created = await ingest(engine, request(case, "main", **overrides))
    await set_received(engine, created.ticket_id, NOW)
    classification = good().model_copy(update={"intent": expect.intent})
    if case == 29:  # the model cannot read it: unsure, and no workflow applies
        classification = classification.model_copy(update={"confidence": 0.4})
    client = Client([output()], expect.intent)
    client._classification = classification
    await (await drafting_handler(engine, client))(job_for(created.ticket_id))
    return created.ticket_id, await row(engine, created.ticket_id), client


@pytest.mark.parametrize("case", range(1, 32))
async def test_the_case_lands_where_the_policy_says(engine: AsyncEngine, case: int) -> None:
    expect = EXPECTED[case]
    ticket_id, ticket, client = await process(engine, case, expect)
    drafts = await drafts_of(engine, ticket_id)
    kinds = [e.event_type for e in await events(engine, ticket_id)]

    assert (ticket.status, ticket.escalation_reason) == (expect.status, expect.reason)
    assert (ticket.order_lookup or {}).get("result") == expect.lookup
    assert tuple(ticket.flags) == expect.flags
    if expect.status == "drafted":
        [draft] = drafts
        assert draft.suggestion_only is expect.suggestion_only
        assert draft.review_status == "pending"
        assert draft.grounding_result["passed"] is True
        assert kinds[-1] == "ticket.drafted"
    else:
        assert drafts == []  # an escalated ticket never gets a draft
        assert client.requests == []  # the drafter is never asked
        assert kinds[-1] == "ticket.escalated"
    assert kinds[0] == "ticket.ingested" and "ticket.classified" in kinds
    assert (await owner_can_approve(engine, ticket_id)) is (
        expect.status == "drafted" and not expect.suggestion_only
    )


async def owner_can_approve(engine: AsyncEngine, ticket_id: UUID) -> bool:
    """A draft the owner may approve with one click: pending and not suggestion-only."""
    return any(
        d.review_status == "pending" and not d.suggestion_only
        for d in await drafts_of(engine, ticket_id)
    )


def test_every_required_case_has_an_expectation() -> None:
    assert sorted(EXPECTED) == sorted(SCENARIOS) == list(range(1, 32))


async def test_a_duplicate_message_is_one_ticket_and_one_draft(engine: AsyncEngine) -> None:
    req = request(22, "dup")
    first, again = await ingest(engine, req), await ingest(engine, req)
    assert (first.created, again.created, again.ticket_id) == (True, False, first.ticket_id)
    client = Client([output(), output()])
    handle = await drafting_handler(engine, client)
    await handle(job_for(first.ticket_id))
    await handle(job_for(again.ticket_id))
    assert len(await drafts_of(engine, first.ticket_id)) == 1
    assert len(client.requests) == 1


async def test_a_reply_joins_its_thread_and_a_photo_in_it_is_recorded(
    engine: AsyncEngine,
) -> None:
    ticket_id, ticket, _ = await process(engine, 28, EXPECTED[28])
    async with engine.connect() as conn:
        threads = (
            await conn.execute(
                text(
                    "SELECT count(*) AS n, bool_or(has_photo) AS photo FROM tickets"
                    " WHERE thread_id = :t"
                ),
                {"t": ticket.thread_id},
            )
        ).one()
    assert (threads.n, threads.photo) == (2, True)
    assert ticket.has_photo is True


async def test_a_damage_report_without_a_photo_is_drafted_without_one_on_record(
    engine: AsyncEngine,
) -> None:
    _, ticket, _ = await process(engine, 9, EXPECTED[9])
    assert ticket.has_photo is False
