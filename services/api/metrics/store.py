"""Load the rows the dashboard is computed from."""

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.db.tables import audit_log, drafts, tickets
from services.api.metrics.compute import DraftRow, EventRow, TicketRow, compute
from services.api.metrics.models import MetricsReport
from services.api.time_utils import IST

# Only the events a metric reads: the rest of the audit trail is not loaded.
EVENT_TYPES = (
    "ticket.classified",
    "ticket.escalated",
    "ticket.drafted",
    "draft.attempt_failed",
    "ticket.mock_sent",
)


def day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """The range as instants: midnight IST at the start of `start` to midnight after `end`."""
    first = datetime(start.year, start.month, start.day, tzinfo=IST)
    after = datetime(end.year, end.month, end.day, tzinfo=IST) + timedelta(days=1)
    return first, after


class MetricsStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def report(self, start: date, end: date) -> MetricsReport:
        first, after = day_bounds(start, end)
        in_range = select(tickets.c.ticket_id).where(
            tickets.c.deleted_at.is_(None),
            tickets.c.received_at >= first,
            tickets.c.received_at < after,
        )
        async with self._engine.connect() as conn:
            ticket_rows = [
                TicketRow(
                    r.ticket_id, r.received_at, r.status, r.intent, r.urgency, r.escalation_reason
                )
                for r in await conn.execute(
                    select(
                        tickets.c.ticket_id,
                        tickets.c.received_at,
                        tickets.c.status,
                        tickets.c.intent,
                        tickets.c.urgency,
                        tickets.c.escalation_reason,
                    ).where(tickets.c.ticket_id.in_(in_range))
                )
            ]
            draft_rows = [
                DraftRow(
                    r.ticket_id,
                    r.attempt,
                    r.kind,
                    r.reply_text,
                    r.final_text,
                    r.review_status,
                    r.citations,
                )
                for r in await conn.execute(
                    select(
                        drafts.c.ticket_id,
                        drafts.c.attempt,
                        drafts.c.kind,
                        drafts.c.reply_text,
                        drafts.c.final_text,
                        drafts.c.review_status,
                        drafts.c.citations,
                    ).where(drafts.c.ticket_id.in_(in_range))
                )
            ]
            event_rows = [
                EventRow(r.ticket_id, r.event_type, r.occurred_at, r.payload)
                for r in await conn.execute(
                    select(
                        audit_log.c.ticket_id,
                        audit_log.c.event_type,
                        audit_log.c.occurred_at,
                        audit_log.c.payload,
                    ).where(
                        audit_log.c.ticket_id.in_(in_range),
                        audit_log.c.event_type.in_(EVENT_TYPES),
                    )
                )
            ]
        return compute(start, end, ticket_rows, draft_rows, event_rows)
