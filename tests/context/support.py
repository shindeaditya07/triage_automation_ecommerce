"""The real mock store, served in-process, for tests that look orders up."""

import importlib
from datetime import UTC, datetime
from typing import Any

import httpx

from services.api.context.client import OrderStoreClient

mock_main = importlib.import_module("services.mock-store.main")
mock_seed = importlib.import_module("services.mock-store.seed")

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
BASE_URL = "http://mock-store.test"


def seeded() -> Any:
    return mock_seed.build_seed(NOW)


def store_client(handler: Any = None) -> tuple[OrderStoreClient, httpx.AsyncClient]:
    """A client for the in-process mock store, or for `handler` (an httpx mock handler)."""
    transport = (
        httpx.MockTransport(handler)
        if handler is not None
        else httpx.ASGITransport(app=mock_main.create_app(NOW))
    )
    http = httpx.AsyncClient(transport=transport, timeout=5)
    return OrderStoreClient(http, BASE_URL), http
