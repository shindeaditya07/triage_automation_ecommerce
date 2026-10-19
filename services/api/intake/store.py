"""Writing a cleaned message to Postgres: the ticket, its audit entry and its job, together."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.api.db.tables import audit_log, tickets
from services.api.intake.cleaning import CleaningReport
from services.api.jobs.store import JobStore, input_hash
from services.api.models.enums import JobType
from services.api.models.ticket import TicketCreateRequest


@dataclass(frozen=True)
class IngestResult:
    ticket_id: UUID
    thread_id: UUID
    created: bool  # False: this message had already been received


class TicketStore(Protocol):
    async def add(
        self, request: TicketCreateRequest, report: CleaningReport, now: datetime
    ) -> IngestResult: ...


class PostgresTicketStore:
    def __init__(self, engine: AsyncEngine, jobs: JobStore) -> None:
        self._engine = engine
        self._jobs = jobs

    async def add(
        self, request: TicketCreateRequest, report: CleaningReport, now: datetime
    ) -> IngestResult:
        """Store the ticket and queue its processing in one transaction: a ticket never exists
        without its job, and a failed enqueue leaves nothing behind. Receiving a message twice
        (same `external_message_id`) returns the first ticket and changes nothing."""
        async with self._engine.begin() as conn:
            thread_id = await self._thread_for(conn, request)
            row = (
                await conn.execute(
                    insert(tickets)
                    .values(
                        ticket_id=uuid4(),
                        external_message_id=request.external_message_id,
                        thread_id=thread_id,
                        in_reply_to=request.in_reply_to,
                        channel=request.channel.value,
                        received_at=request.received_at,
                        customer_email=request.customer.email,
                        customer_name=request.customer.name,
                        subject=request.subject,
                        body=request.body,
                        order_id_hint=request.order_id_hint,
                        attachments=[item.model_dump() for item in request.attachments],
                        has_photo=any(item.is_image for item in request.attachments),
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=[tickets.c.external_message_id])
                    .returning(tickets.c.ticket_id, tickets.c.thread_id)
                )
            ).first()
            if row is None:
                return await self._existing(conn, request.external_message_id)
            ticket_id: UUID = row.ticket_id
            thread_id = row.thread_id
            await conn.execute(
                insert(audit_log).values(
                    ticket_id=ticket_id,
                    event_type="ticket.ingested",
                    actor="system",
                    payload=report.as_payload(request.channel.value),
                    occurred_at=now,
                )
            )
            await self._jobs.enqueue(
                JobType.PROCESS_TICKET,
                ticket_id=ticket_id,
                input_digest=input_hash({"message": request.external_message_id}),
                now=now,
                conn=conn,
            )
            return IngestResult(ticket_id, thread_id, created=True)

    @staticmethod
    async def _thread_for(conn: AsyncConnection, request: TicketCreateRequest) -> UUID:
        """The thread of the message this one replies to, if that message came from the same
        customer; otherwise a new thread. A reply never joins someone else's conversation."""
        if request.in_reply_to:
            found = await conn.scalar(
                select(tickets.c.thread_id).where(
                    tickets.c.external_message_id == request.in_reply_to,
                    tickets.c.customer_email == request.customer.email,
                    tickets.c.deleted_at.is_(None),
                )
            )
            if found is not None:
                return found  # type: ignore[no-any-return]
        return uuid4()

    @staticmethod
    async def _existing(conn: AsyncConnection, external_message_id: str) -> IngestResult:
        row = (
            await conn.execute(
                select(tickets.c.ticket_id, tickets.c.thread_id).where(
                    tickets.c.external_message_id == external_message_id
                )
            )
        ).one()
        return IngestResult(row.ticket_id, row.thread_id, created=False)
