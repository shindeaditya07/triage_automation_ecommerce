"""The owner's decisions on a draft. Each is one transaction with its audit event, and applies
only to a draft still pending on a ticket still `drafted`, so a double click or two tabs cannot
approve and reject the same draft or approve text that changed after it was checked."""

import difflib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.api.db.tables import audit_log, drafts, order_snapshots, tickets
from services.api.grounding.edit import EditContext, EditOverride
from services.api.models.draft import Citation
from services.api.models.enums import DraftKind, ProposedAction, ReviewStatus, TicketStatus
from services.api.tickets.store import needs_original_viewed

MAX_DIFF_CHARS = 20_000
OWNER_AUTHOR = "owner"  # what the model, prompt and policy columns say for a reply no model wrote


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


@dataclass(frozen=True)
class EscalatedState:
    """An escalated ticket the owner may answer: it has no draft, so `draft_id` is the id the
    owner's reply will get."""

    ticket_status: TicketStatus
    escalation_reason: str | None
    snapshot_id: UUID | None
    edit: EditContext

    @property
    def draft_id(self) -> UUID:
        return self.edit.draft_id


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

    async def load_escalated(self, ticket_id: UUID) -> EscalatedState | None:
        """The ticket and its latest order snapshot, to check an owner's reply against. None when
        the ticket is gone."""
        query = select(
            tickets.c.status,
            tickets.c.escalation_reason,
            tickets.c.customer_email,
            tickets.c.subject,
            tickets.c.body,
        ).where(tickets.c.ticket_id == ticket_id, tickets.c.deleted_at.is_(None))
        snapshot = (
            select(order_snapshots.c.snapshot_id, order_snapshots.c.data)
            .where(order_snapshots.c.ticket_id == ticket_id)
            .order_by(order_snapshots.c.taken_at.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).first()
            order = (await conn.execute(snapshot)).first()
        if row is None:
            return None
        return EscalatedState(
            ticket_status=TicketStatus(row.status),
            escalation_reason=row.escalation_reason,
            snapshot_id=order.snapshot_id if order else None,
            edit=EditContext(
                draft_id=uuid4(),
                customer_email=row.customer_email,
                subject=row.subject,
                body=row.body,
                order=order.data if order else None,
                citations=[],
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

    async def owner_reply(
        self,
        ticket_id: UUID,
        state: EscalatedState,
        text: str,
        actor: str,
        now: datetime,
        override: EditOverride | None,
    ) -> None:
        """Record the owner's own reply to an escalated ticket as an approved draft, and move the
        ticket to approved. The mock send follows, as for any approval."""
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    select(tickets.c.status)
                    .where(tickets.c.ticket_id == ticket_id, tickets.c.deleted_at.is_(None))
                    .with_for_update()
                )
            ).first()
            if row is None or row.status != TicketStatus.ESCALATED.value:
                raise NotOpen
            attempt: int = (
                await conn.execute(
                    select(func.coalesce(func.max(drafts.c.attempt), 0) + 1).where(
                        drafts.c.ticket_id == ticket_id
                    )
                )
            ).scalar_one()
            await conn.execute(
                insert(drafts).values(
                    draft_id=state.draft_id,
                    ticket_id=ticket_id,
                    attempt=attempt,
                    kind=DraftKind.OWNER.value,
                    suggestion_only=False,
                    reply_text=text,
                    citations=[],
                    proposed_action=ProposedAction.NONE.value,
                    confidence=1,
                    prompt_version=OWNER_AUTHOR,
                    prompt_hash=OWNER_AUTHOR,
                    model=OWNER_AUTHOR,
                    policy_version=OWNER_AUTHOR,
                    order_snapshot_id=state.snapshot_id,
                    review_status=ReviewStatus.APPROVED.value,
                    final_text=text,
                    reviewed_by=actor,
                    reviewed_at=now,
                    created_at=now,
                )
            )
            await conn.execute(
                update(tickets)
                .where(tickets.c.ticket_id == ticket_id)
                .values(status=TicketStatus.APPROVED.value, updated_at=now)
            )
            await self._event(
                conn,
                ticket_id,
                "ticket.owner_replied",
                actor,
                now,
                {
                    "draft_id": str(state.draft_id),
                    "escalation_reason": state.escalation_reason,
                    "chars": len(text),
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

    async def mock_send(self, ticket_id: UUID, now: datetime) -> bool:
        """Record that the approved reply was "sent" (spec FR-13): no message leaves the system.
        Returns False when there is nothing to do: not approved, or already sent, so a retry is
        harmless."""
        async with self._engine.begin() as conn:
            ticket = (
                await conn.execute(
                    select(tickets.c.status)
                    .where(tickets.c.ticket_id == ticket_id, tickets.c.deleted_at.is_(None))
                    .with_for_update()
                )
            ).first()
            if ticket is None or ticket.status != TicketStatus.APPROVED.value:
                return False
            draft = (
                await conn.execute(
                    update(drafts)
                    .where(
                        drafts.c.ticket_id == ticket_id,
                        drafts.c.review_status == ReviewStatus.APPROVED.value,
                        drafts.c.sent_at.is_(None),
                    )
                    .values(sent_at=now)
                    .returning(drafts.c.draft_id, drafts.c.reviewed_by, drafts.c.final_text)
                )
            ).first()
            if draft is None:
                return False
            await conn.execute(
                update(tickets)
                .where(tickets.c.ticket_id == ticket_id)
                .values(status=TicketStatus.SENT.value, updated_at=now)
            )
            await self._event(
                conn,
                ticket_id,
                "ticket.mock_sent",
                "system",
                now,
                {
                    "draft_id": str(draft.draft_id),
                    "approved_by": draft.reviewed_by,
                    "final_text_chars": len(draft.final_text),
                },
            )
        return True

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
