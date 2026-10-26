"""The `jobs` table: enqueue, claim, finish, and recover jobs whose worker went away.

Every state change is one SQL statement or one short transaction. A job is claimed with
`SELECT ... FOR UPDATE SKIP LOCKED`, so two workers never get the same row and neither waits for
the other. Time comes from the caller's clock, not the database's, so tests control it.

Job states (`JobStatus`):
  queued     waiting for `run_at`
  running    claimed by a worker; `locked_at` is set exactly while this holds
  failed     the last attempt raised; it will run again at `run_at` (backed off)
  succeeded  done
  dead       out of attempts, or failed in a way retrying cannot fix; `last_error` says why

A job that goes dead also marks its ticket `failed` with the reason, so the owner sees it.
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.api.db.tables import jobs, tickets
from services.api.models.enums import JobStatus, JobType, TicketStatus
from services.api.models.records import Job

MAX_ERROR_LENGTH = 500
# Tickets past these states are never pulled back to `failed` by a late job failure.
_FAILABLE_TICKET_STATES = (TicketStatus.NEW, TicketStatus.CLASSIFIED, TicketStatus.DRAFTED)
LOCK_EXPIRED = "worker stopped responding (lock expired)"


def input_hash(value: object) -> str:
    """A stable short hash of a job's input, for the dedupe key."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def dedupe_key(job_type: JobType, ticket_id: UUID | None, input_digest: str) -> str:
    return f"{job_type.value}:{ticket_id or '-'}:{input_digest}"


def _clip(message: str) -> str:
    return message if len(message) <= MAX_ERROR_LENGTH else message[: MAX_ERROR_LENGTH - 1] + "…"


class JobStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def enqueue(
        self,
        job_type: JobType,
        *,
        ticket_id: UUID | None,
        input_digest: str,
        now: datetime,
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
        conn: AsyncConnection | None = None,
    ) -> tuple[UUID, bool]:
        """Add a job. Returns `(job_id, created)`; `created` is False when a job with the same
        type, ticket and input already exists (whatever state it is in), which is returned
        instead of adding another.

        Pass `conn` to enqueue inside the caller's transaction, so the job exists if and only if
        the row that needs it does."""
        key = dedupe_key(job_type, ticket_id, input_digest)
        insert_stmt = (
            insert(jobs)
            .values(
                job_type=job_type.value,
                ticket_id=ticket_id,
                dedupe_key=key,
                payload=payload or {},
                max_attempts=max_attempts,
                run_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[jobs.c.dedupe_key])
            .returning(jobs.c.job_id)
        )
        if conn is not None:
            return await self._insert_or_find(conn, insert_stmt, key)
        async with self._engine.begin() as own:
            return await self._insert_or_find(own, insert_stmt, key)

    @staticmethod
    async def _insert_or_find(
        conn: AsyncConnection, insert_stmt: Any, key: str
    ) -> tuple[UUID, bool]:
        created = (await conn.execute(insert_stmt)).scalar()
        if created is not None:
            return created, True
        existing = await conn.execute(select(jobs.c.job_id).where(jobs.c.dedupe_key == key))
        return existing.scalar_one(), False

    async def claim(self, worker_id: str, now: datetime) -> Job | None:
        """Take the oldest job that is due, or None. Counts the attempt as it is taken."""
        due = (
            select(jobs.c.job_id)
            .where(
                jobs.c.status.in_((JobStatus.QUEUED.value, JobStatus.FAILED.value)),
                jobs.c.run_at <= now,
            )
            .order_by(jobs.c.run_at, jobs.c.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        async with self._engine.begin() as conn:
            job_id = (await conn.execute(due)).scalar()
            if job_id is None:
                return None
            row = (
                await conn.execute(
                    update(jobs)
                    .where(jobs.c.job_id == job_id)
                    .values(
                        status=JobStatus.RUNNING.value,
                        locked_at=now,
                        locked_by=worker_id,
                        attempts=jobs.c.attempts + 1,
                        updated_at=now,
                    )
                    .returning(*jobs.c)
                )
            ).one()
            return Job.model_validate(dict(row._mapping))

    async def succeed(self, job: Job, now: datetime) -> bool:
        """Mark a claimed job done. False if the job is no longer this worker's (it was
        recovered by the sweeper and may have run again), in which case nothing changes."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(jobs)
                .where(self._still_held(job))
                .values(
                    status=JobStatus.SUCCEEDED.value,
                    locked_at=None,
                    locked_by=None,
                    last_error=None,
                    updated_at=now,
                )
            )
            return result.rowcount == 1

    async def fail(
        self,
        job: Job,
        error: str,
        now: datetime,
        *,
        retry_in: timedelta | None,
    ) -> JobStatus | None:
        """Record a failed attempt. With `retry_in` and attempts left the job goes back in the
        queue after that delay; otherwise it is dead and its ticket is marked failed.
        Returns the new status, or None if the job is no longer this worker's."""
        error = _clip(error)
        retry = retry_in is not None and job.attempts < job.max_attempts
        values: dict[str, Any] = {
            "status": (JobStatus.FAILED if retry else JobStatus.DEAD).value,
            "locked_at": None,
            "locked_by": None,
            "last_error": error,
            "updated_at": now,
        }
        if retry and retry_in is not None:
            values["run_at"] = now + retry_in
        async with self._engine.begin() as conn:
            result = await conn.execute(update(jobs).where(self._still_held(job)).values(**values))
            if result.rowcount != 1:
                return None
            if retry:
                return JobStatus.FAILED
            await _fail_ticket(conn, job.ticket_id, job.job_type, job.attempts, error, now)
            return JobStatus.DEAD

    async def recover_stale(self, lock_timeout: timedelta, now: datetime) -> tuple[int, int]:
        """Take back jobs whose worker stopped without finishing them: put them in the queue
        for another try, or, if they are out of attempts, mark them dead. Safe to run from
        several processes at once. Returns `(requeued, dead)`."""
        stale = and_(
            jobs.c.status == JobStatus.RUNNING.value, jobs.c.locked_at < now - lock_timeout
        )
        out_of_attempts = jobs.c.attempts >= jobs.c.max_attempts
        async with self._engine.begin() as conn:
            rows = (
                await conn.execute(
                    update(jobs)
                    .where(stale)
                    .values(
                        status=case(
                            (out_of_attempts, JobStatus.DEAD.value), else_=JobStatus.QUEUED.value
                        ),
                        run_at=case((out_of_attempts, jobs.c.run_at), else_=now),
                        locked_at=None,
                        locked_by=None,
                        last_error=LOCK_EXPIRED,
                        updated_at=now,
                    )
                    .returning(jobs.c.status, jobs.c.ticket_id, jobs.c.job_type, jobs.c.attempts)
                )
            ).all()
            dead = [row for row in rows if row.status == JobStatus.DEAD.value]
            for row in dead:
                await _fail_ticket(
                    conn, row.ticket_id, JobType(row.job_type), row.attempts, LOCK_EXPIRED, now
                )
            return len(rows) - len(dead), len(dead)

    async def get(self, job_id: UUID) -> Job | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(jobs).where(jobs.c.job_id == job_id))).first()
        return Job.model_validate(dict(row._mapping)) if row else None

    @staticmethod
    def _still_held(job: Job) -> Any:
        """The row is still the claim this worker made, not one the sweeper gave to someone else."""
        return and_(
            jobs.c.job_id == job.job_id,
            jobs.c.status == JobStatus.RUNNING.value,
            jobs.c.locked_by == job.locked_by,
            jobs.c.attempts == job.attempts,
        )


async def _fail_ticket(
    conn: AsyncConnection,
    ticket_id: UUID | None,
    job_type: JobType,
    attempts: int,
    error: str,
    now: datetime,
) -> None:
    """A ticket whose job gave up shows as failed, with a reason the owner can read."""
    if ticket_id is None:
        return
    reason = _clip(f"{job_type.value} gave up after {attempts} attempt(s): {error}")
    await conn.execute(
        update(tickets)
        .where(
            tickets.c.ticket_id == ticket_id,
            tickets.c.status.in_([state.value for state in _FAILABLE_TICKET_STATES]),
        )
        .values(status=TicketStatus.FAILED.value, failure_reason=reason, updated_at=now)
    )
