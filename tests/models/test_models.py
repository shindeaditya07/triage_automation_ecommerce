from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.api.models import (
    Draft,
    DraftOutput,
    EscalationResult,
    Job,
    Ticket,
    TicketCreateRequest,
)
from services.api.models.enums import EscalationStage, JobStatus, JobType

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _request(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "external_message_id": "<abc@mail>",
        "channel": "email",
        "customer": {"email": "  Asha@Example.COM "},
        "body": "Where is my order?",
    }
    return base | overrides


def _draft_output(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "reply_text": "Hello",
        "confidence": 0.9,
        "prompt_version": "v1",
        "prompt_hash": "abc",
        "model": "m",
        "policy_version": "1.1",
    }
    return base | overrides


def _draft(**overrides: object) -> dict[str, object]:
    return _draft_output() | {"draft_id": uuid4(), "ticket_id": uuid4(), "attempt": 1} | overrides


def test_customer_email_is_trimmed_and_lowercased() -> None:
    assert TicketCreateRequest(**_request()).customer.email == "asha@example.com"  # type: ignore[arg-type]


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TicketCreateRequest(**_request(bogus=1))  # type: ignore[arg-type]


def test_unknown_channel_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TicketCreateRequest(**_request(channel="sms"))  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["2026-10-15T12:00:00", "2026-10-15T17:30:00+05:30"])
def test_timestamps_must_be_utc(value: str) -> None:
    with pytest.raises(ValidationError):
        TicketCreateRequest(**_request(received_at=value))  # type: ignore[arg-type]


def test_utc_timestamp_with_z_is_accepted() -> None:
    req = TicketCreateRequest(**_request(received_at="2026-10-15T12:00:00Z"))  # type: ignore[arg-type]
    assert req.received_at == NOW


def test_ist_offset_object_is_rejected_even_when_equal_instant() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    with pytest.raises(ValidationError):
        TicketCreateRequest(**_request(received_at=NOW.astimezone(ist)))  # type: ignore[arg-type]


def test_ticket_requires_all_or_no_classification() -> None:
    ticket = Ticket(
        ticket_id=uuid4(),
        external_message_id="x",
        thread_id=uuid4(),
        channel="form",  # type: ignore[arg-type]
        received_at=NOW,
        customer={"email": "a@b.com"},  # type: ignore[arg-type]
        body="hi",
    )
    assert ticket.classification is None
    assert ticket.status == "new"
    with pytest.raises(ValidationError):
        Ticket(**{**ticket.model_dump(), "classification": {"intent": "refund"}})


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        DraftOutput(**_draft_output(confidence=1.01))  # type: ignore[arg-type]


def test_draft_output_order_snapshot_is_optional() -> None:
    assert DraftOutput(**_draft_output()).order_snapshot_id is None  # type: ignore[arg-type]


def test_pending_draft_is_valid() -> None:
    assert Draft(**_draft()).review_status == "pending"  # type: ignore[arg-type]


def test_approved_draft_needs_reviewer_and_final_text() -> None:
    with pytest.raises(ValidationError):
        Draft(**_draft(review_status="approved", final_text="x"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Draft(  # type: ignore[arg-type]
            **_draft(review_status="approved", reviewed_by="owner", reviewed_at=NOW)
        )
    ok = Draft(  # type: ignore[arg-type]
        **_draft(review_status="approved", reviewed_by="owner", reviewed_at=NOW, final_text="x")
    )
    assert ok.review_status == "approved"


def test_pending_draft_cannot_carry_reviewer() -> None:
    with pytest.raises(ValidationError):
        Draft(**_draft(reviewed_by="owner", reviewed_at=NOW))  # type: ignore[arg-type]


def test_unapproved_draft_cannot_be_sent() -> None:
    with pytest.raises(ValidationError):
        Draft(**_draft(sent_at=NOW))  # type: ignore[arg-type]


def test_escalation_needs_reason() -> None:
    with pytest.raises(ValidationError):
        EscalationResult(escalate=True, stage=EscalationStage.PRE_LOOKUP)
    assert EscalationResult(escalate=False, stage=EscalationStage.POST_LOOKUP).reason is None


def test_suggestion_only_requires_escalation() -> None:
    with pytest.raises(ValidationError):
        EscalationResult(escalate=False, stage=EscalationStage.PRE_LOOKUP, suggestion_only=True)


def test_job_lock_matches_status() -> None:
    base = {"job_id": uuid4(), "job_type": JobType.PROCESS_TICKET, "run_at": NOW}
    assert Job(**base).status == JobStatus.QUEUED  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Job(**base, status=JobStatus.RUNNING)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Job(**base, locked_at=NOW)  # type: ignore[arg-type]
    assert Job(**base, status=JobStatus.RUNNING, locked_at=NOW)  # type: ignore[arg-type]
