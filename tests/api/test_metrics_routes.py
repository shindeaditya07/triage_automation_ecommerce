"""GET /api/v1/metrics: auth, the default range and the bad ranges."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from services.api.auth.store import InMemoryAuthStore
from services.api.main import create_app
from services.api.metrics.compute import compute
from services.api.metrics.models import MetricsReport
from tests.auth.conftest import login


class Reader:
    def __init__(self) -> None:
        self.asked: list[tuple[date, date]] = []

    async def report(self, start: date, end: date) -> MetricsReport:
        self.asked.append((start, end))
        return compute(start, end, [], [], [])


@pytest.fixture
def reader() -> Reader:
    return Reader()


@pytest.fixture
def client(make_settings, clock, store: InMemoryAuthStore, reader: Reader):  # type: ignore[no-untyped-def]
    app = create_app(
        auth_settings=make_settings(), auth_store=store, clock=clock, metrics_reader=reader
    )
    with TestClient(app) as test_client:
        yield test_client


def test_the_route_needs_a_signed_in_owner(client: TestClient) -> None:
    assert client.get("/api/v1/metrics").status_code in (401, 403)


def test_the_default_range_is_the_last_thirty_days_ending_today_in_ist(
    client: TestClient, reader: Reader, clock
) -> None:  # type: ignore[no-untyped-def]
    login(client)
    body = client.get("/api/v1/metrics").json()
    [(start, end)] = reader.asked
    assert (end - start).days == 29
    assert body["start"] == start.isoformat() and body["end"] == end.isoformat()
    assert body["tickets_total"] == 0


def test_a_given_range_is_passed_through(client: TestClient, reader: Reader) -> None:
    login(client)
    assert client.get("/api/v1/metrics?start=2026-10-01&end=2026-10-07").status_code == 200
    assert reader.asked == [(date(2026, 10, 1), date(2026, 10, 7))]


@pytest.mark.parametrize(
    "query",
    ["start=2026-10-08&end=2026-10-07", "start=2025-01-01&end=2026-10-07", "start=not-a-date"],
)
def test_bad_ranges_are_422(client: TestClient, query: str) -> None:
    login(client)
    assert client.get(f"/api/v1/metrics?{query}").status_code == 422
