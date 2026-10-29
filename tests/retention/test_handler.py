import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from services.api.jobs.runner import JobRunner
from services.api.models.enums import JobType
from services.api.models.records import Job
from services.api.retention.handler import make_retention_handler
from services.api.settings import JobSettings

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)


class RecordingSweeper:
    def __init__(self) -> None:
        self.calls: list[tuple[datetime, datetime, int]] = []

    async def sweep(self, cutoff: datetime, now: datetime, retention_days: int) -> int:
        self.calls.append((cutoff, now, retention_days))
        return 0


class RecordingJobs:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, object]] = []

    async def enqueue(self, job_type: JobType, **kwargs: object) -> tuple[object, bool]:
        self.enqueued.append({"job_type": job_type, **kwargs})
        return uuid4(), True


def job() -> Job:
    return Job(job_id=uuid4(), ticket_id=None, job_type=JobType.RETENTION_SWEEP, run_at=NOW)


def test_the_sweep_erases_tickets_received_before_the_retention_period() -> None:
    sweeper = RecordingSweeper()
    handler = make_retention_handler(sweeper, 90, lambda: NOW)
    asyncio.run(handler(job()))
    assert sweeper.calls == [(NOW - timedelta(days=90), NOW, 90)]


def runner(jobs: RecordingJobs, clock) -> JobRunner:  # type: ignore[no-untyped-def]
    return JobRunner(jobs, {}, JobSettings(), clock)  # type: ignore[arg-type]


def test_the_sweep_is_queued_once_per_utc_day() -> None:
    jobs = RecordingJobs()
    asyncio.run(runner(jobs, lambda: NOW).schedule_retention())
    asyncio.run(runner(jobs, lambda: NOW + timedelta(hours=5)).schedule_retention())
    asyncio.run(runner(jobs, lambda: NOW + timedelta(days=1)).schedule_retention())
    digests = [call["input_digest"] for call in jobs.enqueued]
    assert all(call["job_type"] is JobType.RETENTION_SWEEP for call in jobs.enqueued)
    assert all(call["ticket_id"] is None for call in jobs.enqueued)
    assert digests[0] == digests[1] != digests[2]


def test_a_queueing_failure_is_logged_not_raised() -> None:
    class Broken:
        async def enqueue(self, *args: object, **kwargs: object) -> None:
            raise OSError("database down")

    asyncio.run(JobRunner(Broken(), {}, JobSettings(), lambda: NOW).schedule_retention())  # type: ignore[arg-type]
