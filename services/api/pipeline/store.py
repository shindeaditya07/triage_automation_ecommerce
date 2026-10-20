"""What the pipeline reads and writes on a ticket. Each write is one transaction with its audit
event, and only applies to a ticket still in the state the step expects, so a job that runs twice
changes nothing the second time."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.context.lookup import ContextLookup, LookupResult
from services.api.db.tables import audit_log, drafts, order_snapshots, tickets
from services.api.llm.client import CallRecord
from services.api.models import CustomerHistory, Order
from services.api.models.draft import DraftOutput
from services.api.models.enums import Flag, Intent, Sentiment, TicketStatus, Urgency
from services.api.models.ticket import Classification


@dataclass(frozen=True)
class TicketText:
    ticket_id: UUID
    status: TicketStatus
    subject: str | None
    body: str
    received_at: datetime
    classification: Classification | None = None  # set once the ticket is classified
    customer_email: str = ""
    order_id_hint: str | None = None
    lookup_done: bool = False
    escalation_reason: str | None = None  # set on a classified ticket routed suggestion-only


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
                        tickets.c.received_at,
                        tickets.c.escalation_reason,
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
            row.received_at,
            classification,
            row.customer_email,
            row.order_id_hint,
            row.lookup_done,
            row.escalation_reason,
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

    async def load_lookup(self, ticket_id: UUID) -> ContextLookup | None:
        """The recorded lookup, rebuilt from the ticket summary and its order snapshot. None when
        the lookup has not run."""
        async with self._engine.connect() as conn:
            summary = (
                await conn.execute(
                    select(tickets.c.order_lookup).where(tickets.c.ticket_id == ticket_id)
                )
            ).scalar_one_or_none()
            if summary is None:
                return None
            order = None
            if summary["snapshot_id"] is not None:
                data = (
                    await conn.execute(
                        select(order_snapshots.c.data).where(
                            order_snapshots.c.snapshot_id == UUID(summary["snapshot_id"])
                        )
                    )
                ).scalar_one()
                order = Order.model_validate(data)
        history = summary["history"]
        return ContextLookup(
            LookupResult(summary["result"]),
            summary["order_id"],
            summary["candidates"],
            order,
            CustomerHistory.model_validate(history) if history is not None else None,
        )

    async def snapshot_id(self, ticket_id: UUID) -> UUID | None:
        """The order snapshot the lookup took, or None when it found no order."""
        async with self._engine.connect() as conn:
            summary = (
                await conn.execute(
                    select(tickets.c.order_lookup).where(tickets.c.ticket_id == ticket_id)
                )
            ).scalar_one_or_none()
        raw = summary["snapshot_id"] if summary else None
        return UUID(raw) if raw else None

    async def save_draft(
        self,
        ticket_id: UUID,
        draft: DraftOutput,
        *,
        passage_ids: list[str],
        calls: list[CallRecord],
        suggestion_only: bool,
        now: datetime,
    ) -> bool:
        """Store the first draft with what it was made from, and move the ticket from
        `classified` to `drafted`. False when the ticket was not `classified` (already drafted or
        escalated), in which case nothing changes."""
        call = next((c for c in reversed(calls) if c.purpose == "draft"), None)
        async with self._engine.begin() as conn:
            moved = await conn.execute(
                update(tickets)
                .where(
                    tickets.c.ticket_id == ticket_id,
                    tickets.c.status == TicketStatus.CLASSIFIED.value,
                    tickets.c.deleted_at.is_(None),
                )
                .values(status=TicketStatus.DRAFTED.value, updated_at=now)
            )
            if moved.rowcount != 1:
                return False
            draft_id = uuid4()
            await conn.execute(
                insert(drafts).values(
                    draft_id=draft_id,
                    ticket_id=ticket_id,
                    attempt=1,
                    suggestion_only=suggestion_only,
                    reply_text=draft.reply_text,
                    citations=[c.model_dump(mode="json") for c in draft.citations],
                    proposed_action=draft.proposed_action.value,
                    confidence=draft.confidence,
                    prompt_version=draft.prompt_version,
                    prompt_hash=draft.prompt_hash,
                    model=draft.model,
                    policy_version=draft.policy_version,
                    order_snapshot_id=draft.order_snapshot_id,
                    retrieved_passage_ids=passage_ids,
                    input_tokens=call.input_tokens if call else None,
                    output_tokens=call.output_tokens if call else None,
                    latency_ms=call.latency_ms if call else None,
                    created_at=now,
                )
            )
            await conn.execute(
                insert(audit_log).values(
                    ticket_id=ticket_id,
                    event_type="ticket.drafted",
                    actor="system",
                    payload={
                        "draft_id": str(draft_id),
                        "suggestion_only": suggestion_only,
                        "passages": passage_ids,
                        "calls": [c.audit_payload() for c in calls],
                    },
                    occurred_at=now,
                )
            )
        return True

    async def count_thread_messages(self, ticket_id: UUID) -> int:
        """The ticket's own message and every earlier one on its thread (ESC-8.2)."""
        this = tickets.alias("this")
        async with self._engine.connect() as conn:
            return int(
                (
                    await conn.execute(
                        select(func.count())
                        .select_from(tickets)
                        .join(
                            this,
                            (tickets.c.thread_id == this.c.thread_id)
                            & (tickets.c.received_at <= this.c.received_at),
                        )
                        .where(this.c.ticket_id == ticket_id, tickets.c.deleted_at.is_(None))
                    )
                ).scalar_one()
            )

    async def mark_suggestion_only(
        self,
        ticket_id: UUID,
        reason: str,
        triggers: list[str],
        flags: list[Flag],
        now: datetime,
    ) -> bool:
        """Route a classified ticket to the owner with a suggestion-only draft (ESC-8.3). The
        ticket stays `classified`; `escalation_reason` is what marks the route. False when the
        ticket was already routed or is no longer classified."""
        values: dict[str, Any] = {
            "escalation_reason": reason,
            "flags": [flag.value for flag in flags],
            "updated_at": now,
        }
        payload: dict[str, Any] = {"stage": "post_lookup", "reason": reason, "triggers": triggers}
        return await self._advance(
            ticket_id,
            values,
            "ticket.suggestion_only",
            payload,
            now,
            TicketStatus.CLASSIFIED,
            unrouted=True,
        )

    async def _advance(
        self,
        ticket_id: UUID,
        values: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
        from_status: TicketStatus = TicketStatus.NEW,
        *,
        unrouted: bool = False,
    ) -> bool:
        guards = [
            tickets.c.ticket_id == ticket_id,
            tickets.c.status == from_status.value,
            tickets.c.deleted_at.is_(None),
        ]
        if unrouted:
            guards.append(tickets.c.escalation_reason.is_(None))
        async with self._engine.begin() as conn:
            result = await conn.execute(update(tickets).where(*guards).values(**values))
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
