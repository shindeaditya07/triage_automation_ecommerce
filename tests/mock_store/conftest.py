import importlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

# The directory is named "mock-store" (per the build plan), so it cannot be imported with a plain
# `import` statement.
main = importlib.import_module("services.mock-store.main")
seed = importlib.import_module("services.mock-store.seed")

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)  # a Thursday; 17:30 on 15 October in IST
IST = ZoneInfo("Asia/Kolkata")

Scenarios = dict[int, Any]


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(main.create_app(NOW))


@pytest.fixture(scope="session")
def seed_data() -> Any:
    return seed.build_seed(NOW)


@pytest.fixture(scope="session")
def scenarios(seed_data: Any) -> Scenarios:
    return {s.case: s for s in seed_data.scenarios}


@pytest.fixture
def order_for(client: TestClient, scenarios: Scenarios) -> Callable[[int], dict[str, Any]]:
    """The order JSON for a case, fetched the way the pipeline fetches it."""

    def fetch(case: int) -> dict[str, Any]:
        scenario = scenarios[case]
        response = client.get(
            f"/orders/{scenario.order_id}", params={"email": scenario.customer_email}
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    return fetch


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def hours_between(later: str, earlier: str) -> float:
    return (parse(later) - parse(earlier)) / timedelta(hours=1)


def ist_date(value: str | datetime) -> Any:
    moment = parse(value) if isinstance(value, str) else value
    return moment.astimezone(IST).date()
