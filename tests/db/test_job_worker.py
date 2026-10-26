"""The worker, the sweeper loop and the app wiring, on real Postgres."""

import asyncio
import time
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from services.api.jobs import worker as worker_module
from services.api.jobs.runner import JobRunner
from services.api.jobs.store import LOCK_EXPIRED, JobStore
from services.api.jobs.worker import Handler, JobWorker, PermanentJobError
from services.api.main import create_app
from services.api.models.enums import JobStatus, JobType
from services.api.models.records import Job
from services.api.settings import JobSettings
from tests.db.jobs_support import T0, FakeClock, make_ticket, ticket_row

pytestmark = pytest.mark.anyio

PROCESS = JobType.PROCESS_TICKET


def _worker(engine: AsyncEngine, clock: FakeClock, handler: Handler | None) -> JobWorker:
    handlers = {PROCESS: handler} if handler else {}
    return JobWorker(JobStore(engine), handlers, clock, worker_id="test-worker")


async def _only_job(store: JobStore, job_id: UUID) -> Job:
    job = await store.get(job_id)
    assert job is not None
    return job


async def test_a_handler_that_returns_marks_the_job_succeeded(engine: AsyncEngine) -> None:
    store, clock, seen = JobStore(engine), FakeClock(), []

    async def handler(job: Job) -> None:
        seen.append(job.ticket_id)

    ticket = await make_ticket(engine)
    job_id, _ = await store.enqueue(PROCESS, ticket_id=ticket, input_digest="d", now=T0)
    assert await _worker(engine, clock, handler).run_once() is True
    assert seen == [ticket]
    assert (await _only_job(store, job_id)).status is JobStatus.SUCCEEDED
    assert await _worker(engine, clock, handler).run_once() is False


async def test_a_failing_handler_is_retried_with_backoff_and_then_fails_the_ticket(
    engine: AsyncEngine,
) -> None:
    store, clock = JobStore(engine), FakeClock()

    async def handler(job: Job) -> None:
        raise ValueError("boom")

    worker = _worker(engine, clock, handler)
    ticket = await make_ticket(engine)
    job_id, _ = await store.enqueue(PROCESS, ticket_id=ticket, input_digest="d", now=T0)

    await worker.run_once()
    first = await _only_job(store, job_id)
    assert (first.status, first.attempts, first.last_error) == (
        JobStatus.FAILED,
        1,
        "ValueError: boom",
    )
    assert first.run_at == T0 + timedelta(seconds=15)
    assert await worker.run_once() is False  # not due yet
    assert (await ticket_row(engine, ticket))[0] == "new"

    clock.advance(seconds=15)
    await worker.run_once()
    assert (await _only_job(store, job_id)).run_at == clock.now + timedelta(seconds=30)

    clock.advance(seconds=30)
    await worker.run_once()
    last = await _only_job(store, job_id)
    assert (last.status, last.attempts) == (JobStatus.DEAD, 3)
    status, reason = await ticket_row(engine, ticket)
    assert status == "failed"
    assert reason == "process_ticket gave up after 3 attempt(s): ValueError: boom"


async def test_a_permanent_error_gives_up_at_once(engine: AsyncEngine) -> None:
    store = JobStore(engine)

    async def handler(job: Job) -> None:
        raise PermanentJobError("ticket body is empty")

    ticket = await make_ticket(engine)
    job_id, _ = await store.enqueue(PROCESS, ticket_id=ticket, input_digest="d", now=T0)
    await _worker(engine, FakeClock(), handler).run_once()
    job = await _only_job(store, job_id)
    assert (job.status, job.attempts, job.last_error) == (
        JobStatus.DEAD,
        1,
        "PermanentJobError: ticket body is empty",
    )
    assert (await ticket_row(engine, ticket))[0] == "failed"


async def test_a_job_with_no_handler_is_dead_with_a_reason(engine: AsyncEngine) -> None:
    store = JobStore(engine)
    job_id, _ = await store.enqueue(PROCESS, ticket_id=None, input_digest="d", now=T0)
    await _worker(engine, FakeClock(), None).run_once()
    job = await _only_job(store, job_id)
    assert (job.status, job.last_error) == (JobStatus.DEAD, "no handler for process_ticket")


async def test_run_works_through_the_queue_then_stops_when_told(engine: AsyncEngine) -> None:
    store, done = JobStore(engine), []

    async def handler(job: Job) -> None:
        done.append(job.job_id)

    ids = [
        (await store.enqueue(PROCESS, ticket_id=None, input_digest=str(n), now=T0))[0]
        for n in range(3)
    ]
    stop = asyncio.Event()
    task = asyncio.create_task(_worker(engine, FakeClock(), handler).run(stop, poll_interval=0.01))
    for _ in range(200):
        if len(done) == 3:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert done == ids


async def test_run_survives_the_queue_being_unreachable(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, calls = JobStore(engine), []
    real_claim = store.claim

    async def flaky_claim(worker_id: str, now):  # type: ignore[no-untyped-def]
        calls.append(1)
        if len(calls) == 1:
            raise OSError("connection refused")
        return await real_claim(worker_id, now)

    monkeypatch.setattr(store, "claim", flaky_claim)
    monkeypatch.setattr(worker_module, "ERROR_BACKOFF_SECONDS", 0.01)
    done: list[UUID] = []

    async def handler(job: Job) -> None:
        done.append(job.job_id)

    await store.enqueue(PROCESS, ticket_id=None, input_digest="d", now=T0)
    stop = asyncio.Event()
    worker = JobWorker(store, {PROCESS: handler}, FakeClock(), worker_id="w")
    task = asyncio.create_task(worker.run(stop, poll_interval=0.01))
    for _ in range(200):
        if done:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert len(done) == 1


async def test_starting_the_runner_recovers_jobs_the_last_process_left_running(
    engine: AsyncEngine,
) -> None:
    store, clock = JobStore(engine), FakeClock()
    job_id, _ = await store.enqueue(PROCESS, ticket_id=None, input_digest="d", now=T0)
    assert await store.claim("old-process", T0) is not None
    clock.advance(seconds=61)
    settings = JobSettings(  # type: ignore[call-arg]
        _env_file=None, lock_timeout_seconds=60, sweep_interval_seconds=3600
    )
    runner = JobRunner(store, {}, settings, clock)
    await runner.sweep()
    job = await _only_job(store, job_id)
    assert (job.status, job.last_error) == (JobStatus.QUEUED, LOCK_EXPIRED)


async def test_the_periodic_sweeper_recovers_a_job_while_running(engine: AsyncEngine) -> None:
    store, clock = JobStore(engine), FakeClock()
    job_id, _ = await store.enqueue(PROCESS, ticket_id=None, input_digest="d", now=T0)
    assert await store.claim("old-process", T0) is not None
    clock.advance(seconds=120)

    async def handler(job: Job) -> None:
        return None

    settings = JobSettings(  # type: ignore[call-arg]
        _env_file=None,
        lock_timeout_seconds=60,
        sweep_interval_seconds=1,
        job_poll_interval_seconds=0.05,
    )
    runner = JobRunner(store, {PROCESS: handler}, settings, clock)
    await runner.start()
    try:
        for _ in range(300):
            if (await _only_job(store, job_id)).status is JobStatus.SUCCEEDED:
                break
            await asyncio.sleep(0.05)
    finally:
        await runner.stop()
    job = await _only_job(store, job_id)
    assert (job.status, job.attempts) == (JobStatus.SUCCEEDED, 2)


def _wait_for_status(url: str, job_id: UUID, wanted: JobStatus, seconds: float) -> JobStatus:
    async def status() -> JobStatus:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            job = await JobStore(engine).get(job_id)
            assert job is not None
            return job.status
        finally:
            await engine.dispose()

    deadline = time.monotonic() + seconds
    current = asyncio.run(status())
    while current is not wanted and time.monotonic() < deadline:
        time.sleep(0.05)
        current = asyncio.run(status())
    return current


def _enqueue_sync(url: str) -> UUID:
    async def run() -> UUID:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            from services.api.clock import utc_now

            job_id, _ = await JobStore(engine).enqueue(
                PROCESS, ticket_id=None, input_digest=uuid4().hex, now=utc_now()
            )
            return job_id
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_the_app_runs_jobs_while_it_is_up(migrated_url: str) -> None:
    ran: list[UUID] = []

    async def handler(job: Job) -> None:
        ran.append(job.job_id)

    async def clean() -> None:
        engine = create_async_engine(migrated_url, poolclass=NullPool)
        async with engine.begin() as conn:
            from sqlalchemy import text

            await conn.execute(text("DELETE FROM jobs"))
        await engine.dispose()

    asyncio.run(clean())
    engine = create_async_engine(migrated_url, poolclass=NullPool)
    settings = JobSettings(_env_file=None, job_poll_interval_seconds=0.05)  # type: ignore[call-arg]
    app = create_app(engine, job_settings=settings, job_handlers={PROCESS: handler})
    with TestClient(app):
        job_id = _enqueue_sync(migrated_url)
        assert (
            _wait_for_status(migrated_url, job_id, JobStatus.SUCCEEDED, 10) is JobStatus.SUCCEEDED
        )
    assert ran == [job_id]
    asyncio.run(engine.dispose())


def test_a_worker_switched_off_leaves_jobs_alone(migrated_url: str) -> None:
    engine = create_async_engine(migrated_url, poolclass=NullPool)
    settings = JobSettings(  # type: ignore[call-arg]
        _env_file=None, job_poll_interval_seconds=0.05, job_worker_enabled=False
    )
    with TestClient(create_app(engine, job_settings=settings)):
        job_id = _enqueue_sync(migrated_url)
        assert _wait_for_status(migrated_url, job_id, JobStatus.SUCCEEDED, 0.5) is JobStatus.QUEUED
    asyncio.run(engine.dispose())
