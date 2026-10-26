"""Starts and stops the worker and the sweeper inside the API process."""

import asyncio
import logging
from collections.abc import Mapping
from datetime import timedelta

from services.api.clock import Clock
from services.api.jobs.store import JobStore
from services.api.jobs.worker import Handler, JobWorker, sleep_or_stop
from services.api.models.enums import JobType
from services.api.settings import JobSettings

log = logging.getLogger("replydesk.jobs")

SHUTDOWN_GRACE_SECONDS = 10.0


class JobRunner:
    def __init__(
        self,
        store: JobStore,
        handlers: Mapping[JobType, Handler],
        settings: JobSettings,
        clock: Clock,
    ) -> None:
        self._store = store
        self._settings = settings
        self._clock = clock
        self._worker = JobWorker(store, handlers, clock)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def sweep(self) -> None:
        """Recover jobs whose worker stopped. A failure here is logged, not raised."""
        try:
            requeued, dead = await self._store.recover_stale(
                timedelta(seconds=self._settings.lock_timeout_seconds), self._clock()
            )
        except Exception:
            log.exception("job sweep failed")
            return
        if requeued or dead:
            log.warning("job sweep: %d re-queued, %d gave up", requeued, dead)

    async def _sweep_forever(self) -> None:
        while not self._stop.is_set():
            await sleep_or_stop(self._stop, self._settings.sweep_interval_seconds)
            if not self._stop.is_set():
                await self.sweep()

    async def start(self) -> None:
        """Sweep once now (jobs left running by the previous process), then start both loops."""
        await self.sweep()
        self._tasks = [
            asyncio.create_task(
                self._worker.run(self._stop, self._settings.job_poll_interval_seconds),
                name="job-worker",
            ),
            asyncio.create_task(self._sweep_forever(), name="job-sweeper"),
        ]

    async def stop(self, grace: float = SHUTDOWN_GRACE_SECONDS) -> None:
        """Let the job in progress finish if it can within `grace` seconds, then stop. A job
        cut off here is picked up again by the sweeper."""
        self._stop.set()
        _, pending = await asyncio.wait(self._tasks, timeout=grace)
        for task in pending:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
