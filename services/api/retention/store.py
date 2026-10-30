"""The retention sweep: ticket data older than the retention period is erased in place.

"Deleted" here means erased, not hidden: the customer's address, name, message, the order data
copied from the store and every reply text are overwritten, and the ticket is marked `deleted_at`
so every read skips it. The row itself stays, because the audit log is append-only and points at
it. The audit entries hold no customer text (card, bank, UPI and Aadhaar numbers are masked before
anything is stored, and rejected draft text is never kept), so they survive and show that a ticket
existed and what was done to it.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Text, case, cast, func, null, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.api.db.tables import audit_log, drafts, order_snapshots, tickets

ERASED_TEXT = "[deleted under the retention policy]"
ERASED_EMAIL = "deleted@invalid"
BATCH = 200


class RetentionStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def sweep(self, cutoff: datetime, now: datetime, retention_days: int) -> int:
        """Erase every live ticket received before `cutoff`, `BATCH` at a time, one transaction
        each so a long backlog never holds one large lock. Records each ticket and the run in the
        audit log. Returns how many tickets were erased."""
        erased = 0
        while True:
            count = await self._batch(cutoff, now, retention_days)
            erased += count
            if count < BATCH:
                break
        async with self._engine.begin() as conn:
            await conn.execute(
                audit_log.insert().values(
                    ticket_id=None,
                    event_type="retention.sweep",
                    actor="system",
                    payload={
                        "retention_days": retention_days,
                        "cutoff": cutoff.isoformat(),
                        "erased": erased,
                    },
                    occurred_at=now,
                )
            )
        return erased

    async def _batch(self, cutoff: datetime, now: datetime, retention_days: int) -> int:
        async with self._engine.begin() as conn:
            ids: list[UUID] = list(
                await conn.scalars(
                    select(tickets.c.ticket_id)
                    .where(tickets.c.received_at < cutoff, tickets.c.deleted_at.is_(None))
                    .order_by(tickets.c.received_at)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
            )
            if not ids:
                return 0
            await erase_tickets(
                conn,
                ids,
                now,
                event_type="ticket.retention_erased",
                actor="system",
                payload={"retention_days": retention_days},
            )
            return len(ids)


async def erase_tickets(
    conn: AsyncConnection,
    ids: Sequence[UUID],
    now: datetime,
    *,
    event_type: str,
    actor: str,
    payload: dict[str, Any],
    placeholder: str = ERASED_TEXT,
) -> None:
    """Overwrite every customer-supplied text of these tickets and record one audit row each.
    Shared by the retention sweep and the customer erasure request, so both leave the same
    result. The caller owns the transaction and has locked the rows."""
    await conn.execute(
        update(tickets)
        .where(tickets.c.ticket_id.in_(ids))
        .values(
            external_message_id=func.concat("deleted:", cast(tickets.c.ticket_id, Text)),
            in_reply_to=None,
            customer_email=ERASED_EMAIL,
            customer_name=None,
            subject=None,
            body=placeholder,
            order_id_hint=None,
            attachments=[],
            order_lookup=null(),
            deleted_at=now,
            updated_at=now,
        )
    )
    await conn.execute(
        update(drafts)
        .where(drafts.c.ticket_id.in_(ids))
        .values(
            reply_text=placeholder,
            final_text=case((drafts.c.final_text.is_(None), null()), else_=placeholder),
            citations=[],
            grounding_result=null(),
        )
    )
    await conn.execute(
        update(order_snapshots).where(order_snapshots.c.ticket_id.in_(ids)).values(data={})
    )
    await conn.execute(
        audit_log.insert(),
        [
            {
                "ticket_id": ticket_id,
                "event_type": event_type,
                "actor": actor,
                "payload": payload,
                "occurred_at": now,
            }
            for ticket_id in ids
        ],
    )
