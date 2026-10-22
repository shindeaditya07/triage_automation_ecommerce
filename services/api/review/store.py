"""The owner's decisions on a draft. Each is one transaction with its audit event, and applies
only to a draft still pending on a ticket still `drafted`, so a double click or two tabs cannot
approve and reject the same draft or approve text that changed after it was checked."""

import difflib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.api.db.tables import audit_log, drafts, order_snapshots, tickets
from services.api.grounding.edit import EditContext, EditOverride
from services.api.models.draft import Citation
from services.api.models.enums import ReviewStatus, TicketStatus
from services.api.tickets.store import needs_original_viewed

MAX_DIFF_CHARS = 20_000


class NotOpen(Exception):
    """The draft is not open for review: decided already, or the ticket moved on."""


@dataclass(frozen=True)
class ReviewState:
    ticket_status: TicketStatus
    review_status: ReviewStatus
    suggestion_only: bool
    needs_original_viewed: bool
    reply_text: str  # the draft as the model wrote it
    final_text: str | None  # the owner's saved edit, if any
    edit: EditContext

    @property
    def draft_id(self) -> UUID:
        return self.edit.draft_id

    @property
    def effective_text(self) -> str:
        return self.final_text if self.final_text is not None else self.reply_text

    @property
    def open(self) -> bool:
        return (
            self.ticket_status is TicketStatus.DRAFTED
            and self.review_status is ReviewStatus.PENDING
        )


def edit_diff(original: str, final: str) -> str:
    lines = difflib.unified_diff(
        original.splitlines(), final.splitlines(), "draft", "final", lineterm="", n=1
    )
    return "\n".join(lines)[:MAX_DIFF_CHARS]


class ReviewStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def load(self, ticket_id: UUID) -> ReviewState | None:
        """The ticket's latest draft and what it is checked against. None when the ticket is gone
        or has no draft."""
        query = (
            select(
                drafts,
                tickets.c.status.label("ticket_status"),
                tickets.c.flags,
                tickets.c.escalation_reason,
                tickets.c.customer_email,
                tickets.c.subject,
                tickets.c.body,
                order_snapshots.c.data.label("order_data"),
            )
            .select_from(
                drafts.join(tickets, tickets.c.ticket_id == drafts.c.ticket_id).outerjoin(
                    order_snapshots, order_snapshots.c.snapshot_id == drafts.c.order_snapshot_id
                )
            )
            .where(drafts.c.ticket_id == ticket_id, tickets.c.deleted_at.is_(None))
            .order_by(drafts.c.attempt.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).first()
        if row is None:
            return None
        return ReviewState(
            ticket_status=TicketStatus(row.ticket_status),
            review_status=ReviewStatus(row.review_status),
            suggestion_only=row.suggestion_only,
            needs_original_viewed=needs_original_viewed(
                row.ticket_status, row.flags, row.escalation_reason
            )
            or row.suggestion_only,
            reply_text=row.reply_text,
            final_text=row.final_text,
            edit=EditContext(
                draft_id=row.draft_id,
                customer_email=row.customer_email,
                subject=row.subject,
                body=row.body,
                order=row.order_data,
                citations=[Citation.model_validate(c) for c in row.citations],
            ),
        )

    async def override_rules(self, draft_id: UUID) -> set[str]:
        """The rules the owner has overridden on this draft's saved text (the latest override)."""
        query = (
            select(audit_log.c.payload)
            .where(
                audit_log.c.event_type == "draft.edit_override",
                audit_log.c.payload["draft_id"].astext == str(draft_id),
            )
            .order_by(audit_log.c.event_id.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            payload = (await conn.execute(query)).scalar()
        return set(payload["overridden_rules"]) if payload else set()

    async def save_edit(
        self,
        ticket_id: UUID,
        state: ReviewState,
        text: str,
        actor: str,
        now: datetime,
        override: EditOverride | None,
    ) -> None:
        async with self._engine.begin() as conn:
            await self._close_check(conn, ticket_id)
            result = await conn.execute(
                update(drafts)
                .where(
                    drafts.c.draft_id == state.draft_id,
                    drafts.c.review_status == ReviewStatus.PENDING.value,
                )
                .values(final_text=text)
            )
            if result.rowcount != 1:
                raise NotOpen
            await self._event(
                conn,
                ticket_id,
                "draft.edited",
                actor,
                now,
                {
                    "draft_id": str(state.draft_id),
                    "diff": edit_diff(state.reply_text, text),
                },
            )
            if override is not None:
                await self._event(
                    conn,
                    ticket_id,
                    "draft.edit_override",
                    override.actor,
                    override.timestamp,
                    {
                        "draft_id": str(state.draft_id),
                        "reason": override.reason,
                        "overridden_rules": override.overridden_rules,
                    },
                )

    async def approve(self, ticket_id: UUID, state: ReviewState, actor: str, now: datetime) -> str:
        """Approve the text that was checked. Returns it."""
        text = state.effective_text
        async with self._engine.begin() as conn:
            await self._close_check(conn, ticket_id)
            result = await conn.execute(
                update(drafts)
                .where(
                    drafts.c.draft_id == state.draft_id,
                    drafts.c.review_status == ReviewStatus.PENDING.value,
                    func.coalesce(drafts.c.final_text, drafts.c.reply_text) == text,
                )
                .values(
                    review_status=ReviewStatus.APPROVED.value,
                    final_text=text,
                    reviewed_by=actor,
                    reviewed_at=now,
                )
            )
            if result.rowcount != 1:
                raise NotOpen
            await self._move_ticket(conn, ticket_id, TicketStatus.APPROVED, now)
            await self._event(
                conn,
                ticket_id,
                "ticket.approved",
                actor,
                now,
                {
                    "draft_id": str(state.draft_id),
                    "edited": text != state.reply_text,
                    "diff": edit_diff(state.reply_text, text) if text != state.reply_text else "",
                    "suggestion_only": state.suggestion_only,
                },
            )
        return text

    async def reject(
        self, ticket_id: UUID, state: ReviewState, reason: str, actor: str, now: datetime
    ) -> None:
        async with self._engine.begin() as conn:
            await self._close_check(conn, ticket_id)
            result = await conn.execute(
                update(drafts)
                .where(
                    drafts.c.draft_id == state.draft_id,
                    drafts.c.review_status == ReviewStatus.PENDING.value,
                )
                .values(
                    review_status=ReviewStatus.REJECTED.value, reviewed_by=actor, reviewed_at=now
                )
            )
            if result.rowcount != 1:
                raise NotOpen
            await self._move_ticket(conn, ticket_id, TicketStatus.REJECTED, now)
            await self._event(
                conn,
                ticket_id,
                "ticket.rejected",
                actor,
                now,
                {"draft_id": str(state.draft_id), "reason": reason},
            )

    @staticmethod
    async def _close_check(conn: AsyncConnection, ticket_id: UUID) -> None:
        """Lock the ticket row so two decisions on it run one after the other."""
        row = (
            await conn.execute(
                select(tickets.c.status)
                .where(tickets.c.ticket_id == ticket_id, tickets.c.deleted_at.is_(None))
                .with_for_update()
            )
        ).first()
        if row is None or row.status != TicketStatus.DRAFTED.value:
            raise NotOpen

    @staticmethod
    async def _move_ticket(
        conn: AsyncConnection, ticket_id: UUID, to: TicketStatus, now: datetime
    ) -> None:
        result = await conn.execute(
            update(tickets)
            .where(
                and_(
                    tickets.c.ticket_id == ticket_id,
                    tickets.c.status == TicketStatus.DRAFTED.value,
                )
            )
            .values(status=to.value, updated_at=now)
        )
        if result.rowcount != 1:
            raise NotOpen

    @staticmethod
    async def _event(
        conn: AsyncConnection,
        ticket_id: UUID,
        event_type: str,
        actor: str,
        now: datetime,
        payload: dict[str, Any],
    ) -> None:
        await conn.execute(
            insert(audit_log).values(
                ticket_id=ticket_id,
                event_type=event_type,
                actor=actor,
                payload=payload,
                occurred_at=now,
            )
        )
