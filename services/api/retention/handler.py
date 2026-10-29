"""The `retention_sweep` job handler."""

import logging
from datetime import datetime, timedelta
from typing import Protocol

from services.api.clock import Clock
from services.api.jobs.worker import Handler
from services.api.models.records import Job

log = logging.getLogger("replydesk.retention")


class Sweeper(Protocol):
    async def sweep(self, cutoff: datetime, now: datetime, retention_days: int) -> int: ...


def make_retention_handler(store: Sweeper, retention_days: int, clock: Clock) -> Handler:
    async def handle(job: Job) -> None:
        now = clock()
        erased = await store.sweep(now - timedelta(days=retention_days), now, retention_days)
        log.info("retention sweep erased %d tickets older than %d days", erased, retention_days)

    return handle
