"""Retrying failed tickets on a real database: where each resumes, and that its job runs again."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.jobs.store import JobStore
from services.api.models.enums import JobStatus, JobType
from services.api.retry.store import NoSuchTicket, NotFailed, RetryStore
from tests.db.jobs_support import T0, make_ticket, ticket_row
from tests.db.test_review_store import drafted

pytestmark = pytest.mark.anyio

ACTOR = "retry-test-owner@example.com"


async def fail_via_job(engine: AsyncEngine, ticket_id) -> JobStore:  # type: ignore[no-untyped-def]
    """Queue the ticket's job and let it die, the way a real failure does."""
    jobs = JobStore(engine)
    await jobs.enqueue(
        JobType.PROCESS_TICKET, ticket_id=ticket_id, input_digest="first", now=T0, max_attempts=1
    )
    job = await jobs.claim("w1", T0)
    assert job is not None and job.ticket_id == ticket_id
    assert await jobs.fail(job, "StoreUnavailable: HTTP 429", T0, retry_in=None) is JobStatus.DEAD
    return jobs


async def audit_kinds(engine: AsyncEngine, ticket_id) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT event_type, actor FROM audit_log WHERE ticket_id = :id ORDER BY event_id"),
            {"id": ticket_id},
        )
        return [(r.event_type, r.actor) for r in rows]


async def test_a_ticket_that_never_got_classified_resumes_as_new(engine: AsyncEngine) -> None:
    ticket = await make_ticket(engine)
    jobs = await fail_via_job(engine, ticket)
    assert (await ticket_row(engine, ticket))[0] == "failed"

    resumed = await RetryStore(engine, jobs).retry(ticket, ACTOR, T0 + timedelta(minutes=5))

    assert resumed.value == "new"
    assert await ticket_row(engine, ticket) == ("new", None)
    assert await audit_kinds(engine, ticket) == [("ticket.retried", ACTOR)]


async def test_the_job_runs_again_with_a_fresh_set_of_attempts(engine: AsyncEngine) -> None:
    ticket = await make_ticket(engine)
    jobs = await fail_via_job(engine, ticket)
    later = T0 + timedelta(minutes=5)
    await RetryStore(engine, jobs).retry(ticket, ACTOR, later)

    job = await jobs.claim("w2", later)

    assert job is not None and job.ticket_id == ticket
    assert (job.attempts, job.last_error) == (1, None)
    assert await jobs.claim("w3", later) is None  # one job, not two


async def test_a_classified_ticket_resumes_as_classified(engine: AsyncEngine) -> None:
    ticket = await make_ticket(engine, "classified")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tickets SET intent = 'return', urgency = 'low', confidence = 0.9,"
                " sentiment = 'neutral' WHERE ticket_id = :id"
            ),
            {"id": ticket},
        )
    jobs = await fail_via_job(engine, ticket)

    resumed = await RetryStore(engine, jobs).retry(ticket, ACTOR, T0)

    assert resumed.value == "classified"
    assert await ticket_row(engine, ticket) == ("classified", None)


async def test_a_ticket_with_a_draft_resumes_as_drafted(engine: AsyncEngine) -> None:
    ticket = await drafted(engine, 1)
    jobs = await fail_via_job(engine, ticket)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tickets SET status = 'failed', failure_reason = 'x' WHERE ticket_id = :id"
            ),
            {"id": ticket},
        )

    resumed = await RetryStore(engine, jobs).retry(ticket, ACTOR, T0)

    assert resumed.value == "drafted"


async def test_a_ticket_that_has_not_failed_is_refused_and_left_alone(engine: AsyncEngine) -> None:
    ticket = await make_ticket(engine, "classified")
    with pytest.raises(NotFailed):
        await RetryStore(engine, JobStore(engine)).retry(ticket, ACTOR, T0)
    assert (await ticket_row(engine, ticket))[0] == "classified"
    assert await audit_kinds(engine, ticket) == []


async def test_an_unknown_or_erased_ticket_is_refused(engine: AsyncEngine) -> None:
    store = RetryStore(engine, JobStore(engine))
    with pytest.raises(NoSuchTicket):
        await store.retry(uuid4(), ACTOR, T0)
    ticket = await make_ticket(engine)
    await fail_via_job(engine, ticket)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tickets SET deleted_at = now() WHERE ticket_id = :id"), {"id": ticket}
        )
    with pytest.raises(NoSuchTicket):
        await store.retry(ticket, ACTOR, T0)


async def test_a_failed_ticket_with_no_job_gets_one(engine: AsyncEngine) -> None:
    ticket = await make_ticket(engine, "failed")
    jobs = JobStore(engine)

    await RetryStore(engine, jobs).retry(ticket, ACTOR, T0)

    job = await jobs.claim("w1", T0)
    assert job is not None and job.ticket_id == ticket
