"""GET /api/v1/tickets and /api/v1/tickets/{id}: auth, parameters, 404 and 503."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.auth.store import InMemoryAuthStore
from services.api.main import create_app
from services.api.models.enums import Channel, TicketStatus
from services.api.tickets.models import AuditEvent, TicketDetail, TicketPage, TicketSummary
from services.api.tickets.store import TicketFilter
from tests.auth.conftest import login

TICKET = uuid4()
SUMMARY = TicketSummary(
    ticket_id=TICKET,
    received_at=datetime(2026, 10, 15, tzinfo=UTC),
    channel=Channel.FORM,
    customer_email="a@example.com",
    customer_name=None,
    subject="Where is it",
    status=TicketStatus.DRAFTED,
    intent=None,
    urgency=None,
    escalation_reason=None,
    flags=[],
    has_draft=True,
    suggestion_only=False,
    used_fallback_template=False,
)


class Reader:
    def __init__(self) -> None:
        self.filters: list[tuple[TicketFilter, int, int]] = []

    async def list_tickets(self, flt: TicketFilter, limit: int, offset: int) -> TicketPage:
        self.filters.append((flt, limit, offset))
        return TicketPage(items=[SUMMARY], total=1, limit=limit, offset=offset)

    async def get_ticket(self, ticket_id: UUID) -> TicketDetail | None:
        if ticket_id != TICKET:
            return None
        return TicketDetail(
            **SUMMARY.model_dump(
                include={"ticket_id", "received_at", "channel", "customer_email", "customer_name"}
                | {"subject", "status", "intent", "urgency", "escalation_reason", "flags"}
            ),
            thread_id=uuid4(),
            body="hi",
            order_id_hint=None,
            has_photo=False,
            confidence=None,
            sentiment=None,
            failure_reason=None,
            order=None,
            draft=None,
            needs_original_viewed=False,
        )

    async def get_audit(self, ticket_id: UUID) -> list[AuditEvent] | None:
        if ticket_id != TICKET:
            return None
        return [
            AuditEvent(
                event_id=1,
                event_type="ticket.escalated",
                actor="system",
                occurred_at=datetime(2026, 10, 15, tzinfo=UTC),
                payload={"reason": "llm_error:LLMRequestError", "error": "HTTP 404"},
            )
        ]


@pytest.fixture
def reader() -> Reader:
    return Reader()


@pytest.fixture
def client(make_settings, clock, store: InMemoryAuthStore, reader: Reader):  # type: ignore[no-untyped-def]
    app = create_app(
        auth_settings=make_settings(), auth_store=store, clock=clock, ticket_reader=reader
    )
    with TestClient(app) as test_client:
        yield test_client


def test_both_routes_need_a_signed_in_owner(client: TestClient) -> None:
    assert client.get("/api/v1/tickets").status_code in (401, 403)
    assert client.get(f"/api/v1/tickets/{TICKET}").status_code in (401, 403)
    assert client.get(f"/api/v1/tickets/{TICKET}/audit").status_code in (401, 403)


def test_the_queue_passes_its_filters_through(client: TestClient, reader: Reader) -> None:
    login(client)
    response = client.get(
        "/api/v1/tickets?status=drafted&status=escalated&intent=refund&limit=10&offset=20"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    [(flt, limit, offset)] = reader.filters
    assert [s.value for s in flt.statuses] == ["drafted", "escalated"]
    assert flt.intent is not None and flt.intent.value == "refund"
    assert (limit, offset) == (10, 20)


@pytest.mark.parametrize("query", ["status=bogus", "limit=0", "limit=101", "offset=-1", "intent=x"])
def test_bad_parameters_are_422(client: TestClient, query: str) -> None:
    login(client)
    assert client.get(f"/api/v1/tickets?{query}").status_code == 422


def test_one_ticket_and_a_missing_one(client: TestClient) -> None:
    login(client)
    assert client.get(f"/api/v1/tickets/{TICKET}").json()["subject"] == "Where is it"
    assert client.get(f"/api/v1/tickets/{uuid4()}").status_code == 404
    assert client.get("/api/v1/tickets/not-a-uuid").status_code == 422


def test_the_audit_trail_and_a_missing_ticket(client: TestClient) -> None:
    login(client)
    [event] = client.get(f"/api/v1/tickets/{TICKET}/audit").json()
    assert event["event_type"] == "ticket.escalated"
    assert event["payload"]["error"] == "HTTP 404"
    assert client.get(f"/api/v1/tickets/{uuid4()}/audit").status_code == 404
    assert client.get("/api/v1/tickets/not-a-uuid/audit").status_code == 422


def test_without_a_database_it_is_503(make_settings, clock, store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(auth_settings=make_settings(), auth_store=store, clock=clock)
    with TestClient(app) as test_client:
        login(test_client)
        assert test_client.get("/api/v1/tickets").status_code == 503
        assert test_client.get(f"/api/v1/tickets/{TICKET}/audit").status_code == 503
