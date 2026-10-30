"""Customer erasure on a real database: every ticket from the address is scrubbed, nothing else."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.erasure.store import ERASED_AT_REQUEST, ErasureStore
from services.api.retention.store import ERASED_EMAIL
from tests.db.test_retention_store import state
from tests.db.test_review_store import drafted

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
ACTOR = "owner@example.com"


async def email_of(engine: AsyncEngine, ticket_id) -> str:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        return str(
            await conn.scalar(
                text("SELECT customer_email FROM tickets WHERE ticket_id = :id"), {"id": ticket_id}
            )
        )


async def set_email(engine: AsyncEngine, ticket_id, email: str) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tickets SET customer_email = :e WHERE ticket_id = :id"),
            {"e": email, "id": ticket_id},
        )


async def test_every_ticket_from_the_address_is_erased(engine: AsyncEngine) -> None:
    first, second, other = (
        await drafted(engine, 1),
        await drafted(engine, 2),
        await drafted(engine, 3),
    )
    await set_email(engine, first, "repeat@example.com")
    await set_email(engine, second, "repeat@example.com")
    await set_email(engine, other, "someone-else@example.com")
    before = await state(engine, other)

    erased = await ErasureStore(engine).erase_customer("repeat@example.com", ACTOR, NOW)

    assert erased == 2
    for ticket_id in (first, second):
        ticket, draft, snapshot, kinds = await state(engine, ticket_id)
        assert ticket.deleted_at == NOW
        assert (ticket.customer_email, ticket.body) == (ERASED_EMAIL, ERASED_AT_REQUEST)
        assert ticket.customer_name is None and ticket.order_lookup is None
        assert draft.reply_text == ERASED_AT_REQUEST and draft.citations == []
        assert snapshot.data == {}
        assert kinds[-1] == "ticket.customer_erased"
    ticket, draft, snapshot, kinds = await state(engine, other)
    assert ticket.deleted_at is None and ticket.body == before[0].body
    assert kinds == before[3]


async def test_the_request_is_audited_without_the_address(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    await set_email(engine, ticket_id, "audit-me@example.com")

    await ErasureStore(engine).erase_customer("audit-me@example.com", "audit-test", NOW)

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT actor, payload::text AS payload FROM audit_log "
                    "WHERE event_type IN ('customer.erasure', 'ticket.customer_erased') "
                    "AND actor = 'audit-test'"
                ),
            )
        ).all()
    assert len(rows) == 2
    assert all("audit-me" not in r.payload for r in rows)


async def test_an_unknown_address_erases_nothing_and_writes_nothing(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    before = await state(engine, ticket_id)

    assert (
        await ErasureStore(engine).erase_customer("never-wrote-in@example.com", "no-op-test", NOW)
        == 0
    )

    ticket, _, _, kinds = await state(engine, ticket_id)
    assert ticket.deleted_at is None and kinds == before[3]
    async with engine.connect() as conn:
        count = await conn.scalar(text("SELECT count(*) FROM audit_log WHERE actor = 'no-op-test'"))
    assert count == 0


async def test_asking_twice_finds_nothing_the_second_time(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    await set_email(engine, ticket_id, "twice@example.com")
    store = ErasureStore(engine)
    assert await store.erase_customer("twice@example.com", ACTOR, NOW) == 1
    assert await store.erase_customer("twice@example.com", ACTOR, NOW) == 0
