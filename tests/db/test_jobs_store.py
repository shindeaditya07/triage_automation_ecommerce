"""The job table's behaviour on real Postgres: claiming, retrying, giving up, recovering."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.jobs.store import LOCK_EXPIRED, MAX_ERROR_LENGTH, JobStore
from services.api.models.enums import JobStatus, JobType
from tests.db.jobs_support import T0, make_ticket, ticket_row

pytestmark = pytest.mark.anyio

LOCK_TIMEOUT = timedelta(seconds=60)


@pytest.fixture
def store(engine: AsyncEngine) -> JobStore:
    return JobStore(engine)


async def _enqueue(store: JobStore, ticket_id=None, digest="d1", **kwargs):  # type: ignore[no-untyped-def]
    return await store.enqueue(
        JobType.PROCESS_TICKET, ticket_id=ticket_id, input_digest=digest, now=T0, **kwargs
    )


async def test_a_duplicate_enqueue_returns_the_existing_job(
    store: JobStore,
    engine: AsyncEngine,
) -> None:
    ticket = await make_ticket(engine)
    first, created = await _enqueue(store, ticket)
    again, created_again = await _enqueue(store, ticket)
    assert (created, created_again, again) == (True, False, first)


async def test_a_different_input_or_ticket_is_a_different_job(
    store: JobStore,
    engine: AsyncEngine,
) -> None:
    ticket, other = await make_ticket(engine), await make_ticket(engine)
    ids = {
        (await _enqueue(store, ticket, "d1"))[0],
        (await _enqueue(store, ticket, "d2"))[0],
        (await _enqueue(store, other, "d1"))[0],
    }
    assert len(ids) == 3


async def test_a_finished_job_still_blocks_a_duplicate(store: JobStore) -> None:
    job_id, _ = await _enqueue(store)
    job = await store.claim("w1", T0)
    assert job is not None
    await store.succeed(job, T0)
    assert await _enqueue(store) == (job_id, False)


async def test_parallel_enqueues_of_one_job_create_it_once(store: JobStore) -> None:
    results = await asyncio.gather(*(_enqueue(store) for _ in range(10)))
    assert sum(created for _, created in results) == 1
    assert len({job_id for job_id, _ in results}) == 1


async def test_claim_takes_the_oldest_due_job_and_counts_the_attempt(store: JobStore) -> None:
    older, _ = await store.enqueue(
        JobType.PROCESS_TICKET, ticket_id=None, input_digest="a", now=T0 - timedelta(minutes=5)
    )
    await _enqueue(store, digest="b")
    job = await store.claim("w1", T0)
    assert job is not None
    assert (job.job_id, job.status, job.attempts, job.locked_by, job.locked_at) == (
        older,
        JobStatus.RUNNING,
        1,
        "w1",
        T0,
    )


async def test_nothing_is_claimed_before_its_run_time(store: JobStore) -> None:
    await store.enqueue(
        JobType.PROCESS_TICKET,
        ticket_id=None,
        input_digest="a",
        now=T0 + timedelta(seconds=30),
    )
    # run_at is `now` at enqueue time, so a claim from before that finds nothing
    assert await store.claim("w1", T0) is None


async def test_parallel_workers_never_get_the_same_job(store: JobStore) -> None:
    for n in range(5):
        await _enqueue(store, digest=str(n))
    claims = await asyncio.gather(*(store.claim(f"w{n}", T0) for n in range(12)))
    claimed = [job.job_id for job in claims if job is not None]
    assert len(claimed) == 5
    assert len(set(claimed)) == 5


async def test_success_marks_the_job_done_and_clears_the_lock(store: JobStore) -> None:
    job_id, _ = await _enqueue(store)
    job = await store.claim("w1", T0)
    assert job is not None
    assert await store.succeed(job, T0) is True
    done = await store.get(job_id)
    assert done is not None
    assert (done.status, done.locked_at, done.locked_by, done.last_error) == (
        JobStatus.SUCCEEDED,
        None,
        None,
        None,
    )


async def test_a_failure_with_attempts_left_waits_and_then_runs_again(store: JobStore) -> None:
    job_id, _ = await _enqueue(store)
    job = await store.claim("w1", T0)
    assert job is not None
    outcome = await store.fail(job, "boom", T0, retry_in=timedelta(seconds=15))
    assert outcome is JobStatus.FAILED
    assert await store.claim("w2", T0 + timedelta(seconds=14)) is None
    retry = await store.claim("w2", T0 + timedelta(seconds=15))
    assert retry is not None
    assert (retry.job_id, retry.attempts, retry.last_error) == (job_id, 2, "boom")


async def test_a_job_out_of_attempts_goes_dead_and_fails_its_ticket(
    store: JobStore,
    engine: AsyncEngine,
) -> None:
    ticket = await make_ticket(engine)
    job_id, _ = await _enqueue(store, ticket, max_attempts=1)
    job = await store.claim("w1", T0)
    assert job is not None
    assert await store.fail(job, "boom", T0, retry_in=timedelta(seconds=15)) is JobStatus.DEAD
    dead = await store.get(job_id)
    assert dead is not None
    assert (dead.status, dead.last_error, dead.locked_at) == (JobStatus.DEAD, "boom", None)
    status, reason = await ticket_row(engine, ticket)
    assert status == "failed"
    assert reason == "process_ticket gave up after 1 attempt(s): boom"


async def test_no_retry_means_dead_even_with_attempts_left(store: JobStore) -> None:
    job_id, _ = await _enqueue(store)
    job = await store.claim("w1", T0)
    assert job is not None
    assert await store.fail(job, "bad input", T0, retry_in=None) is JobStatus.DEAD
    dead = await store.get(job_id)
    assert dead is not None
    assert dead.attempts == 1


async def test_a_ticket_that_moved_on_is_not_pulled_back_to_failed(
    store: JobStore,
    engine: AsyncEngine,
) -> None:
    ticket = await make_ticket(engine, status="sent")
    await _enqueue(store, ticket, max_attempts=1)
    job = await store.claim("w1", T0)
    assert job is not None
    await store.fail(job, "late failure", T0, retry_in=None)
    assert (await ticket_row(engine, ticket))[0] == "sent"


async def test_a_long_error_is_cut_to_fit(store: JobStore) -> None:
    job_id, _ = await _enqueue(store)
    job = await store.claim("w1", T0)
    assert job is not None
    await store.fail(job, "x" * 5000, T0, retry_in=timedelta(seconds=1))
    failed = await store.get(job_id)
    assert failed is not None
    assert failed.last_error is not None
    assert len(failed.last_error) == MAX_ERROR_LENGTH


async def test_a_stale_job_goes_back_in_the_queue_and_a_fresh_one_is_left_alone(
    store: JobStore,
) -> None:
    stale_id, _ = await _enqueue(store, digest="stale")
    stale = await store.claim("w1", T0)
    assert stale is not None
    fresh_id, _ = await _enqueue(store, digest="fresh")
    later = T0 + LOCK_TIMEOUT + timedelta(seconds=1)
    fresh = await store.claim("w2", later - timedelta(seconds=5))
    assert fresh is not None

    assert await store.recover_stale(LOCK_TIMEOUT, later) == (1, 0)

    recovered = await store.get(stale_id)
    untouched = await store.get(fresh_id)
    assert recovered is not None
    assert untouched is not None
    assert (recovered.status, recovered.last_error, recovered.locked_at) == (
        JobStatus.QUEUED,
        LOCK_EXPIRED,
        None,
    )
    assert untouched.status is JobStatus.RUNNING
    again = await store.claim("w3", later)
    assert again is not None
    assert (again.job_id, again.attempts) == (stale_id, 2)


async def test_a_stale_job_out_of_attempts_goes_dead_and_fails_its_ticket(
    store: JobStore,
    engine: AsyncEngine,
) -> None:
    ticket = await make_ticket(engine)
    job_id, _ = await _enqueue(store, ticket, max_attempts=1)
    assert await store.claim("w1", T0) is not None
    assert await store.recover_stale(LOCK_TIMEOUT, T0 + 2 * LOCK_TIMEOUT) == (0, 1)
    dead = await store.get(job_id)
    assert dead is not None
    assert dead.status is JobStatus.DEAD
    status, reason = await ticket_row(engine, ticket)
    assert status == "failed"
    assert reason is not None
    assert LOCK_EXPIRED in reason


async def test_the_old_worker_cannot_overwrite_a_job_that_was_taken_back(store: JobStore) -> None:
    job_id, _ = await _enqueue(store)
    first = await store.claim("w1", T0)
    assert first is not None
    later = T0 + 2 * LOCK_TIMEOUT
    await store.recover_stale(LOCK_TIMEOUT, later)
    second = await store.claim("w2", later)
    assert second is not None

    assert await store.succeed(first, later) is False
    assert await store.fail(first, "late", later, retry_in=None) is None
    held = await store.get(job_id)
    assert held is not None
    assert (held.status, held.locked_by) == (JobStatus.RUNNING, "w2")
    assert await store.succeed(second, later) is True


async def test_two_sweeps_at_once_recover_each_job_once(store: JobStore) -> None:
    for n in range(4):
        await _enqueue(store, digest=str(n))
        assert await store.claim("w1", T0) is not None
    results = await asyncio.gather(
        *(store.recover_stale(LOCK_TIMEOUT, T0 + 2 * LOCK_TIMEOUT) for _ in range(4))
    )
    assert sum(requeued for requeued, _ in results) == 4
