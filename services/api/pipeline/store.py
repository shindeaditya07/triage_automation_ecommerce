"""What the pipeline reads and writes on a ticket. Each write is one transaction with its audit
event, and only applies to a ticket still in the state the step expects, so a job that runs twice
changes nothing the second time."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.context.lookup import ContextLookup, LookupResult
from services.api.db.tables import audit_log, order_snapshots, tickets
from services.api.llm.client import CallRecord
from services.api.models.enums import Flag, Intent, Sentiment, TicketStatus, Urgency
from services.api.models.ticket import Classification


@dataclass(frozen=True)
class TicketText:
    ticket_id: UUID
    status: TicketStatus
    subject: str | None
    body: str
    classification: Classification | None = None  # set once the ticket is classified
    customer_email: str = ""
    order_id_hint: str | None = None
    lookup_done: bool = False


class PipelineStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_text(self, ticket_id: UUID) -> TicketText | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(
                        tickets.c.status,
                        tickets.c.subject,
                        tickets.c.body,
                        tickets.c.intent,
                        tickets.c.urgency,
                        tickets.c.confidence,
                        tickets.c.sentiment,
                        tickets.c.flags,
                        tickets.c.customer_email,
                        tickets.c.order_id_hint,
                        tickets.c.order_lookup.is_not(None).label("lookup_done"),
                    ).where(tickets.c.ticket_id == ticket_id, tickets.c.deleted_at.is_(None))
                )
            ).first()
        if row is None:
            return None
        classification = None
        if row.intent is not None:
            classification = Classification(
                intent=Intent(row.intent),
                urgency=Urgency(row.urgency),
                confidence=float(row.confidence),
                sentiment=Sentiment(row.sentiment),
                flags=[Flag(flag) for flag in row.flags],
            )
        return TicketText(
            ticket_id,
            TicketStatus(row.status),
            row.subject,
            row.body,
            classification,
            row.customer_email,
            row.order_id_hint,
            row.lookup_done,
        )

    async def save_classification(
        self,
        ticket_id: UUID,
        classification: Classification,
        calls: list[CallRecord],
        dropped_flags: list[str],
        now: datetime,
    ) -> bool:
        """Record the classification and move the ticket from `new` to `classified`.
        False when the ticket was not `new` (already handled), in which case nothing changes."""
        payload: dict[str, Any] = {
            "classification": classification.model_dump(mode="json"),
            "calls": [call.audit_payload() for call in calls],
            "dropped_flags": dropped_flags,
        }
        values = {
            "status": TicketStatus.CLASSIFIED.value,
            "intent": classification.intent.value,
            "urgency": classification.urgency.value,
            "confidence": classification.confidence,
            "sentiment": classification.sentiment.value,
            "flags": [flag.value for flag in classification.flags],
            "updated_at": now,
        }
        return await self._advance(ticket_id, values, "ticket.classified", payload, now)

    async def escalate(
        self,
        ticket_id: UUID,
        stage: str,
        reason: str,
        calls: list[CallRecord],
        now: datetime,
        *,
        from_status: TicketStatus = TicketStatus.NEW,
        flags: list[Flag] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        """Hand a ticket to the owner without a draft, saying why. `flags` replaces the ticket's
        flags (the classifier's plus any the deterministic checks added); `detail` goes into the
        audit event."""
        payload: dict[str, Any] = {
            "stage": stage,
            "reason": reason,
            "calls": [call.audit_payload() for call in calls],
            **(detail or {}),
        }
        values: dict[str, Any] = {
            "status": TicketStatus.ESCALATED.value,
            "escalation_reason": reason,
            "updated_at": now,
        }
        if flags is not None:
            values["flags"] = [flag.value for flag in flags]
        return await self._advance(ticket_id, values, "ticket.escalated", payload, now, from_status)

    async def save_order_lookup(
        self, ticket_id: UUID, lookup: ContextLookup, now: datetime
    ) -> bool:
        """Record what the lookup found: the order snapshot (only when an order was found), the
        lookup summary on the ticket, and `order_mismatch` on a mismatch. The ticket stays
        `classified`. False when the lookup was already recorded, in which case nothing changes."""
        snapshot_id = uuid4() if lookup.order is not None else None
        summary: dict[str, Any] = {
            "result": lookup.result.value,
            "order_id": lookup.order_id,
            "candidates": lookup.candidates,
            "snapshot_id": str(snapshot_id) if snapshot_id else None,
            "history": lookup.history.model_dump(mode="json") if lookup.history else None,
        }
        async with self._engine.begin() as conn:
            current = (
                await conn.execute(
                    select(tickets.c.flags)
                    .where(
                        tickets.c.ticket_id == ticket_id,
                        tickets.c.status == TicketStatus.CLASSIFIED.value,
                        tickets.c.order_lookup.is_(None),
                    )
                    .with_for_update()
                )
            ).first()
            if current is None:
                return False
            flags = list(current.flags)
            if lookup.result is LookupResult.MISMATCH and Flag.ORDER_MISMATCH.value not in flags:
                flags.append(Flag.ORDER_MISMATCH.value)
            if snapshot_id is not None and lookup.order is not None:
                await conn.execute(
                    insert(order_snapshots).values(
                        snapshot_id=snapshot_id,
                        ticket_id=ticket_id,
                        order_id=lookup.order.order_id,
                        taken_at=now,
                        data=lookup.order.model_dump(mode="json"),
                    )
                )
            await conn.execute(
                update(tickets)
                .where(tickets.c.ticket_id == ticket_id)
                .values(order_lookup=summary, flags=flags, updated_at=now)
            )
            await conn.execute(
                insert(audit_log).values(
                    ticket_id=ticket_id,
                    event_type="ticket.order_lookup",
                    actor="system",
                    payload=summary,
                    occurred_at=now,
                )
            )
        return True

    async def _advance(
        self,
        ticket_id: UUID,
        values: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
        from_status: TicketStatus = TicketStatus.NEW,
    ) -> bool:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(tickets)
                .where(
                    tickets.c.ticket_id == ticket_id,
                    tickets.c.status == from_status.value,
                    tickets.c.deleted_at.is_(None),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                return False
            await conn.execute(
                insert(audit_log).values(
                    ticket_id=ticket_id,
                    event_type=event_type,
                    actor="system",
                    payload=payload,
                    occurred_at=now,
                )
            )
        return True
