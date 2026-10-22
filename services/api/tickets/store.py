"""Reads for the owner's queue and ticket screen (soft-deleted tickets are never shown)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, case, func, select, true
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.db.tables import audit_log, drafts, order_snapshots, tickets
from services.api.models.draft import Citation
from services.api.models.enums import (
    DraftKind,
    Flag,
    Intent,
    SourceType,
    TicketStatus,
    Urgency,
)
from services.api.tickets.models import (
    AuditEvent,
    CitedPolicy,
    DraftView,
    TicketDetail,
    TicketPage,
    TicketSummary,
)

# Most urgent first; a ticket that was never classified sorts after every classified one.
_URGENCY_RANK = case(
    *((tickets.c.urgency == u.value, rank) for rank, u in enumerate(reversed(list(Urgency)))),
    else_=len(Urgency),
)


def needs_original_viewed(status: str, flags: Sequence[str], escalation_reason: str | None) -> bool:
    """Escalated, suspected-injection and suggestion-only tickets are not approved until the owner
    has viewed the customer's original message."""
    return (
        status == TicketStatus.ESCALATED.value
        or Flag.PROMPT_INJECTION_SUSPECTED.value in flags
        or escalation_reason is not None
    )


@dataclass(frozen=True)
class TicketFilter:
    statuses: Sequence[TicketStatus] = ()
    intent: Intent | None = None


class TicketReadStore:
    def __init__(self, engine: AsyncEngine, policy_texts: Mapping[str, str]) -> None:
        self._engine = engine
        self._policy_texts = policy_texts

    async def list_tickets(self, flt: TicketFilter, limit: int, offset: int) -> TicketPage:
        where: list[ColumnElement[bool]] = [tickets.c.deleted_at.is_(None)]
        if flt.statuses:
            where.append(tickets.c.status.in_([s.value for s in flt.statuses]))
        if flt.intent is not None:
            where.append(tickets.c.intent == flt.intent.value)
        latest = (
            select(drafts.c.suggestion_only, drafts.c.kind)
            .where(drafts.c.ticket_id == tickets.c.ticket_id)
            .order_by(drafts.c.attempt.desc())
            .limit(1)
            .correlate(tickets)
            .lateral("latest")
        )
        query = (
            select(
                tickets,
                latest.c.suggestion_only,
                latest.c.kind,
                (latest.c.kind.is_not(None)).label("has_draft"),
            )
            .select_from(tickets.outerjoin(latest, true()))
            .where(*where)
            .order_by(_URGENCY_RANK, tickets.c.received_at, tickets.c.ticket_id)
            .limit(limit)
            .offset(offset)
        )
        count = select(func.count()).select_from(tickets).where(*where)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
            total = (await conn.execute(count)).scalar_one()
        items = [
            TicketSummary(
                ticket_id=r.ticket_id,
                received_at=r.received_at,
                channel=r.channel,
                customer_email=r.customer_email,
                customer_name=r.customer_name,
                subject=r.subject,
                status=r.status,
                intent=r.intent,
                urgency=r.urgency,
                escalation_reason=r.escalation_reason,
                flags=list(r.flags),
                has_draft=bool(r.has_draft),
                suggestion_only=bool(r.suggestion_only),
                used_fallback_template=r.kind == DraftKind.FALLBACK_TEMPLATE.value,
            )
            for r in rows
        ]
        return TicketPage(items=items, total=total, limit=limit, offset=offset)

    async def get_ticket(self, ticket_id: UUID) -> TicketDetail | None:
        async with self._engine.connect() as conn:
            t = (
                await conn.execute(
                    select(tickets).where(
                        tickets.c.ticket_id == ticket_id, tickets.c.deleted_at.is_(None)
                    )
                )
            ).first()
            if t is None:
                return None
            d = (
                await conn.execute(
                    select(drafts, order_snapshots.c.data.label("order_data"))
                    .select_from(
                        drafts.outerjoin(
                            order_snapshots,
                            order_snapshots.c.snapshot_id == drafts.c.order_snapshot_id,
                        )
                    )
                    .where(drafts.c.ticket_id == ticket_id)
                    .order_by(drafts.c.attempt.desc())
                    .limit(1)
                )
            ).first()
        draft = self._draft_view(d) if d is not None else None
        return TicketDetail(
            ticket_id=t.ticket_id,
            thread_id=t.thread_id,
            received_at=t.received_at,
            channel=t.channel,
            customer_email=t.customer_email,
            customer_name=t.customer_name,
            subject=t.subject,
            body=t.body,
            order_id_hint=t.order_id_hint,
            has_photo=t.has_photo,
            status=t.status,
            intent=t.intent,
            urgency=t.urgency,
            confidence=float(t.confidence) if t.confidence is not None else None,
            sentiment=t.sentiment,
            flags=list(t.flags),
            escalation_reason=t.escalation_reason,
            failure_reason=t.failure_reason,
            order=d.order_data if d is not None else None,
            draft=draft,
            needs_original_viewed=needs_original_viewed(t.status, t.flags, t.escalation_reason),
        )

    async def get_audit(self, ticket_id: UUID) -> list[AuditEvent] | None:
        """The ticket's audit events, oldest first; None when there is no such ticket."""
        async with self._engine.connect() as conn:
            exists = (
                await conn.execute(
                    select(tickets.c.ticket_id).where(
                        tickets.c.ticket_id == ticket_id, tickets.c.deleted_at.is_(None)
                    )
                )
            ).first()
            if exists is None:
                return None
            rows = (
                await conn.execute(
                    select(audit_log)
                    .where(audit_log.c.ticket_id == ticket_id)
                    .order_by(audit_log.c.occurred_at, audit_log.c.event_id)
                )
            ).all()
        return [
            AuditEvent(
                event_id=r.event_id,
                event_type=r.event_type,
                actor=r.actor,
                occurred_at=r.occurred_at,
                payload=dict(r.payload),
            )
            for r in rows
        ]

    def _draft_view(self, d: Any) -> DraftView:
        citations = [Citation.model_validate(c) for c in d.citations]
        policy_ids = sorted({c.source_ref for c in citations if c.source_type is SourceType.POLICY})
        return DraftView(
            draft_id=d.draft_id,
            attempt=d.attempt,
            kind=d.kind,
            suggestion_only=d.suggestion_only,
            reply_text=d.reply_text,
            final_text=d.final_text,
            citations=citations,
            cited_policies=[
                CitedPolicy(policy_id=pid, text=self._policy_texts.get(pid)) for pid in policy_ids
            ],
            proposed_action=d.proposed_action,
            confidence=float(d.confidence),
            model=d.model,
            policy_version=d.policy_version,
            grounding_result=d.grounding_result,
            review_status=d.review_status,
            reviewed_by=d.reviewed_by,
            reviewed_at=d.reviewed_at,
            created_at=d.created_at,
        )
