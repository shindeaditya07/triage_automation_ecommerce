"""Intake against real Postgres: what is stored, threading, idempotency, and the job."""

import asyncio
import hashlib
import hmac
import json
import time
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from services.api.clock import utc_now
from services.api.intake.service import IntakeService
from services.api.intake.store import PostgresTicketStore
from services.api.jobs.store import JobStore
from services.api.main import create_app
from services.api.models.enums import Channel, JobType
from services.api.models.records import Job
from services.api.models.ticket import Attachment, Customer, TicketCreateRequest
from services.api.settings import IntakeSettings, JobSettings
from tests.db.jobs_support import FakeClock

HMAC_SECRET = "h" * 40  # pragma: allowlist secret
pytestmark = pytest.mark.anyio


class Scope:
    """One test's own messages. Tickets with audit entries cannot be deleted, so every query is
    limited to the tickets this test made."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.tag = uuid4().hex

    def mid(self, name: str) -> str:
        return f"<{self.tag}-{name}@mail>"

    def request(self, name: str | None = None, **overrides: object) -> TicketCreateRequest:
        values: dict[str, object] = {
            "external_message_id": self.mid(name or uuid4().hex),
            "channel": Channel.EMAIL,
            "customer": Customer(email="asha@example.com", name="Asha"),
            "subject": "Order status",
            "body": "Where is KHG-10001?",
        } | overrides
        return TicketCreateRequest(**values)  # type: ignore[arg-type]

    async def rows(self, table: str) -> list[dict[str, object]]:
        sql = {
            "tickets": "SELECT * FROM tickets WHERE external_message_id LIKE :p",
            "jobs": "SELECT j.* FROM jobs j JOIN tickets t ON t.ticket_id = j.ticket_id"
            " WHERE t.external_message_id LIKE :p",
            "audit_log": "SELECT a.* FROM audit_log a JOIN tickets t ON t.ticket_id = a.ticket_id"
            " WHERE t.external_message_id LIKE :p",
        }[table]
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql), {"p": f"<{self.tag}-%"})
            return [dict(row._mapping) for row in result]


@pytest.fixture
def scope(engine: AsyncEngine) -> Scope:
    return Scope(engine)


@pytest.fixture
def service(engine: AsyncEngine) -> IntakeService:
    return IntakeService(PostgresTicketStore(engine, JobStore(engine)), FakeClock())


async def test_a_message_is_stored_cleaned_with_a_job_and_an_audit_entry(
    service: IntakeService, scope: Scope
) -> None:
    result = await service.ingest(
        scope.request(
            body="Refund to <b>ravi@okaxis</b>, card 4111 1111 1111 1111 https://x.example",
            subject="Money back",
        )
    )
    assert result.created
    (ticket,) = await scope.rows("tickets")
    assert ticket["ticket_id"] == result.ticket_id
    assert ticket["body"] == "Refund to [UPI ID removed], card [card number removed] [link removed]"
    assert (ticket["status"], ticket["channel"], ticket["has_photo"]) == ("new", "email", False)
    assert ticket["customer_email"] == "asha@example.com"

    (job,) = await scope.rows("jobs")
    assert (job["ticket_id"], job["job_type"], job["status"]) == (
        result.ticket_id,
        JobType.PROCESS_TICKET.value,
        "queued",
    )

    (audit,) = await scope.rows("audit_log")
    assert (audit["event_type"], audit["actor"]) == ("ticket.ingested", "system")
    assert audit["payload"]["pii_masked"] == {"upi": 1, "card": 1}  # type: ignore[index]
    assert audit["payload"]["links_removed"] == 1  # type: ignore[index]
    assert "4111" not in json.dumps(audit["payload"])


async def test_every_ticket_gets_its_own_eight_digit_complaint_id(
    service: IntakeService, scope: Scope
) -> None:
    for name in ("a", "b", "c", "d", "e"):
        await service.ingest(scope.request(name))
    numbers = [row["complaint_id"] for row in await scope.rows("tickets")]
    assert len(set(numbers)) == 5
    assert all(isinstance(n, int) and 10_000_000 <= n <= 99_999_999 for n in numbers)


async def test_a_message_received_twice_keeps_its_complaint_id(
    service: IntakeService, scope: Scope
) -> None:
    await service.ingest(scope.request("same"))
    (first,) = await scope.rows("tickets")
    await service.ingest(scope.request("same"))
    (again,) = await scope.rows("tickets")
    assert again["complaint_id"] == first["complaint_id"]


async def test_the_original_text_is_not_kept_anywhere(service: IntakeService, scope: Scope) -> None:
    await service.ingest(scope.request(body="my card is 4111111111111111 and PAN ABCPE1234F"))
    dump = json.dumps(
        {table: await scope.rows(table) for table in ("tickets", "jobs", "audit_log")}, default=str
    )
    assert "4111111111111111" not in dump
    assert "ABCPE1234F" not in dump


@pytest.mark.parametrize(
    ("attachments", "expected"),
    [
        ([], False),
        ([("invoice.pdf", "application/pdf")], False),
        ([("broken.jpg", "image/jpeg")], True),
        ([("invoice.pdf", "application/pdf"), ("broken.png", "IMAGE/PNG")], True),
    ],
)
async def test_has_photo_is_set_when_an_attachment_is_an_image(
    service: IntakeService, scope: Scope, attachments: list[tuple[str, str]], expected: bool
) -> None:
    items = [Attachment(filename=n, content_type=t, size_bytes=10) for n, t in attachments]
    await service.ingest(scope.request(attachments=items))
    (ticket,) = await scope.rows("tickets")
    assert ticket["has_photo"] is expected
    assert [a["filename"] for a in ticket["attachments"]] == [n for n, _ in attachments]  # type: ignore[attr-defined]


async def test_the_same_message_twice_is_one_ticket_and_one_job(
    service: IntakeService, scope: Scope
) -> None:
    first = await service.ingest(scope.request("dup"))
    again = await service.ingest(scope.request("dup", body="different text"))
    assert (first.created, again.created) == (True, False)
    assert (again.ticket_id, again.thread_id) == (first.ticket_id, first.thread_id)
    (ticket,) = await scope.rows("tickets")
    assert ticket["body"] == "Where is KHG-10001?"
    assert len(await scope.rows("jobs")) == 1
    assert len(await scope.rows("audit_log")) == 1


async def test_the_same_message_arriving_at_once_is_one_ticket(
    service: IntakeService, scope: Scope
) -> None:
    results = await asyncio.gather(*(service.ingest(scope.request("race")) for _ in range(10)))
    assert sum(r.created for r in results) == 1
    assert len({r.ticket_id for r in results}) == 1
    assert len(await scope.rows("tickets")) == 1
    assert len(await scope.rows("jobs")) == 1


async def test_a_reply_from_the_same_customer_joins_the_thread(
    service: IntakeService, scope: Scope
) -> None:
    first = await service.ingest(scope.request("m1"))
    reply = await service.ingest(scope.request("m2", in_reply_to=scope.mid("m1"), body="Update?"))
    third = await service.ingest(scope.request("m3", in_reply_to=scope.mid("m2"), body="Hello??"))
    assert reply.created
    assert reply.ticket_id != first.ticket_id
    assert reply.thread_id == first.thread_id == third.thread_id
    assert len(await scope.rows("tickets")) == 3
    assert len(await scope.rows("jobs")) == 3


async def test_a_reply_never_joins_another_customers_thread(
    service: IntakeService, scope: Scope
) -> None:
    first = await service.ingest(scope.request("m1"))
    stranger = Customer(email="mallory@example.com")
    reply = await service.ingest(
        scope.request("m2", in_reply_to=scope.mid("m1"), customer=stranger)
    )
    assert reply.thread_id != first.thread_id


async def test_a_reply_to_an_unknown_message_starts_a_new_thread(
    service: IntakeService, scope: Scope
) -> None:
    first = await service.ingest(scope.request("m1"))
    reply = await service.ingest(scope.request("m2", in_reply_to=scope.mid("never-seen")))
    assert reply.thread_id != first.thread_id


async def test_a_ticket_is_never_stored_without_its_job(
    engine: AsyncEngine, scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = JobStore(engine)

    async def broken(*args: object, **kwargs: object) -> tuple[UUID, bool]:
        raise OSError("queue unavailable")

    monkeypatch.setattr(jobs, "enqueue", broken)
    service = IntakeService(PostgresTicketStore(engine, jobs), FakeClock())
    with pytest.raises(OSError, match="queue unavailable"):
        await service.ingest(scope.request())
    for table in ("tickets", "jobs", "audit_log"):
        assert await scope.rows(table) == []


async def test_a_message_that_failed_can_be_received_again(
    engine: AsyncEngine, scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = JobStore(engine)
    real_enqueue = jobs.enqueue
    calls: list[int] = []

    async def flaky(*args: object, **kwargs: object) -> tuple[UUID, bool]:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("down")
        return await real_enqueue(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(jobs, "enqueue", flaky)
    service = IntakeService(PostgresTicketStore(engine, jobs), FakeClock())
    with pytest.raises(OSError, match="down"):
        await service.ingest(scope.request("retry"))
    assert (await service.ingest(scope.request("retry"))).created


def _signed(payload: dict[str, object]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(HMAC_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"Content-Type": "application/json", "X-Signature": signature}


def test_a_signed_email_becomes_a_ticket_and_a_job_the_worker_picks_up(migrated_url: str) -> None:
    """The whole path through the real app, store and worker."""
    handled: list[Job] = []

    async def handler(job: Job) -> None:
        handled.append(job)

    async def clean() -> None:
        engine = create_async_engine(migrated_url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM jobs"))
        await engine.dispose()

    asyncio.run(clean())
    engine = create_async_engine(migrated_url, poolclass=NullPool)
    app = create_app(
        engine,
        intake_settings=IntakeSettings(_env_file=None, hmac_secret=HMAC_SECRET),  # type: ignore[arg-type]
        job_settings=JobSettings(_env_file=None, job_poll_interval_seconds=0.05),  # type: ignore[call-arg]
        job_handlers={JobType.PROCESS_TICKET: handler},
        clock=utc_now,
    )
    payload = {
        "message_id": f"<{uuid4()}@mail>",
        "customer": {"email": "Asha@Example.com"},
        "body": "Where is my order?",
    }
    with TestClient(app) as client:
        body, headers = _signed(payload)
        response = client.post("/api/v1/tickets/email", content=body, headers=headers)
        assert response.status_code == 201
        ticket_id = UUID(response.json()["ticket_id"])
        deadline = time.monotonic() + 10
        while not handled and time.monotonic() < deadline:
            time.sleep(0.05)
    assert [job.ticket_id for job in handled] == [ticket_id]
    asyncio.run(engine.dispose())
