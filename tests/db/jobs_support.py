"""Shared helpers for the job tests: a clock and ticket rows."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


async def make_ticket(engine: AsyncEngine, status: str = "new") -> UUID:
    ticket_id = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tickets (ticket_id, external_message_id, thread_id, channel,"
                " received_at, customer_email, body, status, failure_reason)"
                " VALUES (:id, :msg, :thread, 'email', now(), 'a@b.com', 'hi', :status, :reason)"
            ),
            {
                "id": ticket_id,
                "msg": f"m-{ticket_id}",
                "thread": uuid4(),
                "status": status,
                "reason": "x" if status == "failed" else None,
            },
        )
    return ticket_id


async def ticket_row(engine: AsyncEngine, ticket_id: UUID) -> tuple[str, str | None]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, failure_reason FROM tickets WHERE ticket_id = :id"),
                {"id": ticket_id},
            )
        ).one()
    return row.status, row.failure_reason
