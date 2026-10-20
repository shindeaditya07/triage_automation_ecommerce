"""What the pipeline reads and writes on a ticket. Each write is one transaction with its audit
event, and only applies to a ticket still in the state the step expects, so a job that runs twice
changes nothing the second time."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.db.tables import audit_log, tickets
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
            ticket_id, TicketStatus(row.status), row.subject, row.body, classification
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
