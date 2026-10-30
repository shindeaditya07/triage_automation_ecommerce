"""Erase one customer's tickets on request. Same result as the retention sweep (see
retention/store.py), chosen by the customer's address instead of by age."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.db.tables import audit_log, tickets
from services.api.retention.store import erase_tickets

ERASED_AT_REQUEST = "[erased at the customer's request]"


class ErasureStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def erase_customer(self, email: str, actor: str, now: datetime) -> int:
        """Erase every live ticket from `email` (already normalised) in one transaction and record
        each ticket and the request in the audit log. The request row holds the count, never the
        address. Returns how many tickets were erased; zero writes nothing."""
        async with self._engine.begin() as conn:
            ids: list[UUID] = list(
                await conn.scalars(
                    select(tickets.c.ticket_id)
                    .where(tickets.c.customer_email == email, tickets.c.deleted_at.is_(None))
                    .order_by(tickets.c.received_at)
                    .with_for_update()
                )
            )
            if not ids:
                return 0
            await erase_tickets(
                conn,
                ids,
                now,
                event_type="ticket.customer_erased",
                actor=actor,
                payload={},
                placeholder=ERASED_AT_REQUEST,
            )
            await conn.execute(
                audit_log.insert().values(
                    ticket_id=None,
                    event_type="customer.erasure",
                    actor=actor,
                    payload={"erased": len(ids)},
                    occurred_at=now,
                )
            )
            return len(ids)
