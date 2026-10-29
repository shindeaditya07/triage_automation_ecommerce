"""The retention sweep on a real database: customer data is overwritten, the audit trail stays."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.retention.store import ERASED_EMAIL, ERASED_TEXT, RetentionStore
from tests.db.test_review_store import drafted

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)
OLD = datetime(2020, 1, 1, tzinfo=UTC)
CUTOFF = datetime(2021, 1, 1, tzinfo=UTC)


async def age(engine: AsyncEngine, ticket_id: UUID, received: datetime) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tickets SET received_at = :at WHERE ticket_id = :id"),
            {"at": received, "id": ticket_id},
        )


async def state(engine: AsyncEngine, ticket_id: UUID):  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        ticket = (
            await conn.execute(
                text("SELECT * FROM tickets WHERE ticket_id = :id"), {"id": ticket_id}
            )
        ).one()
        draft = (
            await conn.execute(
                text("SELECT * FROM drafts WHERE ticket_id = :id"), {"id": ticket_id}
            )
        ).one()
        snapshot = (
            await conn.execute(
                text("SELECT data FROM order_snapshots WHERE ticket_id = :id"), {"id": ticket_id}
            )
        ).one()
        kinds = [
            r.event_type
            for r in await conn.execute(
                text("SELECT event_type FROM audit_log WHERE ticket_id = :id ORDER BY event_id"),
                {"id": ticket_id},
            )
        ]
    return ticket, draft, snapshot, kinds


async def test_an_old_ticket_is_erased_and_its_audit_trail_kept(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    await age(engine, ticket_id, OLD)
    before = await state(engine, ticket_id)
    assert before[0].customer_email != ERASED_EMAIL and before[2].data

    erased = await RetentionStore(engine).sweep(CUTOFF, NOW, 90)

    ticket, draft, snapshot, kinds = await state(engine, ticket_id)
    assert erased >= 1
    assert ticket.deleted_at == NOW
    assert (ticket.customer_email, ticket.body) == (ERASED_EMAIL, ERASED_TEXT)
    assert ticket.customer_name is None and ticket.subject is None
    assert ticket.order_lookup is None and ticket.attachments == []
    assert ticket.external_message_id == f"deleted:{ticket_id}"
    assert draft.reply_text == ERASED_TEXT and draft.citations == []
    assert draft.grounding_result is None
    assert snapshot.data == {}
    assert kinds[: len(before[3])] == before[3]
    assert kinds[-1] == "ticket.retention_erased"


async def test_a_recent_ticket_is_left_alone(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    await age(engine, ticket_id, NOW)
    before = await state(engine, ticket_id)

    await RetentionStore(engine).sweep(CUTOFF, NOW, 90)

    ticket, draft, snapshot, kinds = await state(engine, ticket_id)
    assert ticket.deleted_at is None and ticket.body == before[0].body
    assert draft.reply_text == before[1].reply_text and snapshot.data == before[2].data
    assert kinds == before[3]


async def test_a_second_sweep_finds_nothing_and_each_run_is_recorded(
    engine: AsyncEngine,
) -> None:
    ticket_id = await drafted(engine)
    await age(engine, ticket_id, OLD)
    store = RetentionStore(engine)
    await store.sweep(CUTOFF, NOW, 90)

    assert await store.sweep(CUTOFF, NOW, 90) == 0

    async with engine.connect() as conn:
        runs = (
            await conn.execute(
                text(
                    "SELECT payload FROM audit_log WHERE event_type = 'retention.sweep' "
                    "AND ticket_id IS NULL ORDER BY event_id DESC LIMIT 1"
                )
            )
        ).one()
    assert runs.payload["erased"] == 0 and runs.payload["retention_days"] == 90
    _, _, _, kinds = await state(engine, ticket_id)
    assert kinds.count("ticket.retention_erased") == 1
