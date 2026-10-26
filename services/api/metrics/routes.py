"""`GET /api/v1/metrics?start=&end=`: the dashboard numbers for a range of IST days. Owner-only.

Both dates are optional; the default is the last 30 days ending today (IST). A range is at most
366 days so one request cannot read the whole history.
"""

from datetime import date, timedelta
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from services.api.auth.routes import require_owner
from services.api.auth.service import Owner
from services.api.metrics.models import MetricsReport
from services.api.time_utils import ist_date

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])

DEFAULT_DAYS = 30
MAX_DAYS = 366


class MetricsReader(Protocol):
    async def report(self, start: date, end: date) -> MetricsReport: ...


def get_reader(request: Request) -> MetricsReader:
    reader: MetricsReader | None = getattr(request.app.state, "metrics_reader", None)
    if reader is None:
        raise HTTPException(503, "metrics are not available")
    return reader


@router.get("", response_model=MetricsReport)
async def metrics(
    request: Request,
    _: Annotated[Owner, Depends(require_owner)],
    reader: Annotated[MetricsReader, Depends(get_reader)],
    start: Annotated[date | None, Query(description="First day (IST), inclusive.")] = None,
    end: Annotated[date | None, Query(description="Last day (IST), inclusive.")] = None,
) -> MetricsReport:
    last = end or ist_date(request.app.state.clock())
    first = start or last - timedelta(days=DEFAULT_DAYS - 1)
    if first > last:
        raise HTTPException(422, "start is after end")
    if (last - first).days >= MAX_DAYS:
        raise HTTPException(422, f"a range is at most {MAX_DAYS} days")
    return await reader.report(first, last)
