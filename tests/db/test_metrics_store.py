"""The dashboard numbers, read from real drafted tickets."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.db.tables import tickets
from services.api.metrics.store import MetricsStore
from services.api.time_utils import ist_date
from tests.db.test_ticket_reads import drafted

pytestmark = pytest.mark.anyio


async def test_a_drafted_ticket_is_counted_and_timed(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine, 1)
    async with engine.connect() as conn:
        received = (
            await conn.execute(
                select(tickets.c.received_at).where(tickets.c.ticket_id == ticket_id)
            )
        ).scalar_one()
    day = ist_date(received)
    report = await MetricsStore(engine).report(day - timedelta(days=1), day + timedelta(days=1))
    assert report.tickets_total >= 1
    assert sum(c.count for c in report.by_intent) == report.tickets_total
    assert report.time_to_draft_seconds.samples >= 1
    assert report.fallback.denominator >= 1
    assert next(c.key for c in report.grounding_failures) == "G-1"


async def test_a_range_with_no_tickets_is_empty(engine: AsyncEngine) -> None:
    report = await MetricsStore(engine).report(date(2001, 1, 1), date(2001, 1, 2))
    assert report.tickets_total == 0
    assert report.cost.total_inr == 0
    assert report.cost.per_day == []
