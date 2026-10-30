"""Put a failed ticket back where its processing stopped, and queue its job again."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.db.tables import audit_log, drafts, jobs, tickets
from services.api.jobs.store import JobStore, input_hash
from services.api.models.enums import JobStatus, JobType, TicketStatus


class NoSuchTicket(Exception):
    pass


class NotFailed(Exception):
    pass


class RetryStore:
    def __init__(self, engine: AsyncEngine, job_store: JobStore) -> None:
        self._engine = engine
        self._jobs = job_store

    async def retry(self, ticket_id: UUID, actor: str, now: datetime) -> TicketStatus:
        """Move a failed ticket back to the stage it had reached (drafted if it has a draft,
        classified if it was classified, otherwise new), clear its failure reason and give its
        job a fresh set of attempts, all in one transaction. The pipeline resumes from the
        ticket's status, so nothing already done is redone. Returns the status it resumed at."""
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    select(tickets.c.status, tickets.c.intent)
                    .where(tickets.c.ticket_id == ticket_id, tickets.c.deleted_at.is_(None))
                    .with_for_update()
                )
            ).first()
            if row is None:
                raise NoSuchTicket
            if row.status != TicketStatus.FAILED.value:
                raise NotFailed
            has_draft = await conn.scalar(select(exists().where(drafts.c.ticket_id == ticket_id)))
            if has_draft:
                resume = TicketStatus.DRAFTED
            elif row.intent is not None:
                resume = TicketStatus.CLASSIFIED
            else:
                resume = TicketStatus.NEW
            await conn.execute(
                update(tickets)
                .where(tickets.c.ticket_id == ticket_id)
                .values(status=resume.value, failure_reason=None, updated_at=now)
            )
            revived = await conn.execute(
                update(jobs)
                .where(
                    jobs.c.ticket_id == ticket_id,
                    jobs.c.job_type == JobType.PROCESS_TICKET.value,
                    jobs.c.status.in_((JobStatus.DEAD.value, JobStatus.FAILED.value)),
                )
                .values(
                    status=JobStatus.QUEUED.value,
                    attempts=0,
                    run_at=now,
                    locked_at=None,
                    locked_by=None,
                    last_error=None,
                    updated_at=now,
                )
            )
            if revived.rowcount == 0:
                await self._jobs.enqueue(
                    JobType.PROCESS_TICKET,
                    ticket_id=ticket_id,
                    input_digest=input_hash({"retry": now.isoformat()}),
                    now=now,
                    conn=conn,
                )
            await conn.execute(
                audit_log.insert().values(
                    ticket_id=ticket_id,
                    event_type="ticket.retried",
                    actor=actor,
                    payload={"resumed_at": resume.value},
                    occurred_at=now,
                )
            )
            return resume
