import hashlib
import hmac
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.auth.passwords import hash_password
from services.api.clock import Clock
from services.api.intake.cleaning import CleaningReport
from services.api.intake.service import IntakeService
from services.api.intake.store import IngestResult
from services.api.main import create_app
from services.api.models.ticket import TicketCreateRequest
from services.api.settings import IntakeSettings
from tests.auth.conftest import OWNER_PASSWORD, FakeClock

HMAC_SECRET = "h" * 40  # pragma: allowlist secret
OTHER_SECRET = "o" * 40  # pragma: allowlist secret


@dataclass
class Received:
    request: TicketCreateRequest
    report: CleaningReport


@dataclass
class FakeStore:
    """Keeps what intake hands it, and dedupes on the message id like the real store."""

    received: list[Received] = field(default_factory=list)
    ids: dict[str, IngestResult] = field(default_factory=dict)

    async def add(
        self, request: TicketCreateRequest, report: CleaningReport, now: datetime
    ) -> IngestResult:
        known = self.ids.get(request.external_message_id)
        if known is not None:
            return IngestResult(known.ticket_id, known.thread_id, created=False)
        result = IngestResult(uuid4(), uuid4(), created=True)
        self.ids[request.external_message_id] = result
        self.received.append(Received(request, report))
        return result


def sign(body: bytes, secret: str = HMAC_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def email_body(**overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "message_id": f"<{uuid4()}@mail.example>",
        "customer": {"email": "Asha@Example.com", "name": "Asha"},
        "subject": "Where is my order?",
        "body": "Order KHG-10001 has not arrived.",
    } | overrides
    return json.dumps(payload).encode()


def form_body(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Asha",
        "email": "asha@example.com",
        "subject": "Order status",
        "message": "Where is order KHG-10001?",
    } | overrides


@pytest.fixture(scope="session")
def owner_hash() -> str:
    return hash_password(OWNER_PASSWORD)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def make_client(clock: Clock, store: FakeStore) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def make(**overrides: Any) -> TestClient:
        values: dict[str, Any] = {"hmac_secret": HMAC_SECRET, "form_ip_max_per_minute": 100}
        settings = IntakeSettings(_env_file=None, **(values | overrides))  # type: ignore[arg-type]
        app = create_app(intake_settings=settings, intake_service=IntakeService(store, clock))
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()
