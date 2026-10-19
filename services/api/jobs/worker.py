"""Runs queued jobs one at a time. The work itself is a handler registered per job type."""

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from uuid import uuid4

from services.api.clock import Clock
from services.api.jobs.store import JobStore
from services.api.models.enums import JobType
from services.api.models.records import Job

log = logging.getLogger("replydesk.jobs")

Handler = Callable[[Job], Awaitable[None]]

RETRY_BASE_SECONDS = 15
RETRY_MAX_SECONDS = 600
ERROR_BACKOFF_SECONDS = 5.0


class PermanentJobError(Exception):
    """Raise from a handler when trying again cannot help (bad input, a ticket that is gone).
    The job goes straight to dead instead of using its remaining attempts."""


def retry_delay(attempts: int) -> timedelta:
    """Wait before the next try: 15 s after the first failure, doubling, never more than 10 min."""
    return timedelta(seconds=min(RETRY_BASE_SECONDS * 2 ** max(attempts - 1, 0), RETRY_MAX_SECONDS))


def describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


class JobWorker:
    def __init__(
        self,
        store: JobStore,
        handlers: Mapping[JobType, Handler],
        clock: Clock,
        worker_id: str | None = None,
    ) -> None:
        self._store = store
        self._handlers = handlers
        self._clock = clock
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:6]}"

    async def run_once(self) -> bool:
        """Claim and run one job. Returns False when nothing was due."""
        job = await self._store.claim(self.worker_id, self._clock())
        if job is None:
            return False
        handler = self._handlers.get(job.job_type)
        if handler is None:
            await self._store.fail(
                job, f"no handler for {job.job_type.value}", self._clock(), retry_in=None
            )
            return True
        try:
            await handler(job)
        except PermanentJobError as exc:
            await self._store.fail(job, describe(exc), self._clock(), retry_in=None)
        except Exception as exc:
            outcome = await self._store.fail(
                job, describe(exc), self._clock(), retry_in=retry_delay(job.attempts)
            )
            log.warning("job %s failed (attempt %d): %s", job.job_id, job.attempts, outcome)
        else:
            if not await self._store.succeed(job, self._clock()):
                log.warning(
                    "job %s finished after it was taken back; result not recorded", job.job_id
                )
        return True

    async def run(self, stop: asyncio.Event, poll_interval: float) -> None:
        """Work until `stop` is set. A database error is logged and waited out, never fatal."""
        while not stop.is_set():
            try:
                busy = await self.run_once()
            except Exception:
                log.exception("job worker could not reach the queue")
                busy = False
                await sleep_or_stop(stop, ERROR_BACKOFF_SECONDS)
            if not busy:
                await sleep_or_stop(stop, poll_interval)


async def sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)
