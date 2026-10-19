"""Every CHECK, UNIQUE and trigger rule, exercised against a real Postgres."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

from tests.db.conftest import Statement

Attempt = Callable[[list[Statement]], BaseException | None]

TICKET_ID = uuid4()
REVIEWED_AT = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _insert(table: str, values: dict[str, Any], *, now_columns: tuple[str, ...] = ()) -> Statement:
    """An INSERT built from test-controlled column names; the values are bound parameters."""
    cols = list(values)
    placeholders = [f"${i + 1}" for i in range(len(cols))]
    names = ", ".join([*now_columns, *cols])
    markers = ", ".join(["now()"] * len(now_columns) + placeholders)
    sql = f"INSERT INTO {table} ({names}) VALUES ({markers})"  # noqa: S608
    return sql, tuple(values[c] for c in cols)


def ticket(**columns: Any) -> Statement:
    """A valid ticket INSERT, with the given columns overridden or added."""
    values: dict[str, Any] = {
        "ticket_id": TICKET_ID,
        "external_message_id": f"m-{uuid4()}",
        "thread_id": uuid4(),
        "channel": "email",
        "customer_email": "a@b.com",
        "body": "hi",
    } | columns
    return _insert("tickets", values, now_columns=("received_at",))


def draft(**columns: Any) -> Statement:
    values: dict[str, Any] = {
        "ticket_id": TICKET_ID,
        "attempt": 1,
        "reply_text": "hello",
        "confidence": 0.9,
        "prompt_version": "v1",
        "prompt_hash": "h",
        "model": "m",
        "policy_version": "1.1",
    } | columns
    return _insert("drafts", values)


def job(**columns: Any) -> Statement:
    return _insert("jobs", {"job_type": "process_ticket"} | columns)


def violated(error: BaseException | None) -> str:
    """Name of the constraint an error complains about."""
    assert isinstance(error, asyncpg.IntegrityConstraintViolationError), error
    return error.constraint_name or ""


def test_valid_rows_are_accepted(attempt: Attempt) -> None:
    assert attempt([ticket(), draft(), job(ticket_id=TICKET_ID)]) is None


def test_external_message_id_is_unique(attempt: Attempt) -> None:
    first = ticket(external_message_id="same")
    second = ticket(external_message_id="same", ticket_id=uuid4())
    assert violated(attempt([first, second])) == "uq_tickets_external_message_id"


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        ({"channel": "sms"}, "ck_tickets_channel"),
        ({"status": "bogus"}, "ck_tickets_status"),
        ({"customer_email": "A@B.com"}, "ck_tickets_customer_email_normalised"),
        ({"customer_email": " a@b.com"}, "ck_tickets_customer_email_normalised"),
        ({"flags": ["not_a_flag"]}, "ck_tickets_flags"),
        ({"status": "failed"}, "ck_tickets_failed_has_reason"),
        ({"status": "escalated"}, "ck_tickets_escalated_has_reason"),
        ({"intent": "refund"}, "ck_tickets_classification_all_or_none"),
        (
            {"intent": "nonsense", "urgency": "low", "confidence": 0.5, "sentiment": "neutral"},
            "ck_tickets_intent",
        ),
        (
            {"intent": "refund", "urgency": "asap", "confidence": 0.5, "sentiment": "neutral"},
            "ck_tickets_urgency",
        ),
        (
            {"intent": "refund", "urgency": "low", "confidence": 0.5, "sentiment": "happy"},
            "ck_tickets_sentiment",
        ),
        (
            {"intent": "refund", "urgency": "low", "confidence": 1.5, "sentiment": "neutral"},
            "ck_tickets_confidence",
        ),
    ],
)
def test_ticket_rules(attempt: Attempt, columns: dict[str, Any], constraint: str) -> None:
    assert violated(attempt([ticket(**columns)])) == constraint


def test_full_classification_is_accepted(attempt: Attempt) -> None:
    columns = {
        "intent": "refund",
        "urgency": "high",
        "confidence": 0.8,
        "sentiment": "negative",
        "flags": ["angry", "high_value"],
    }
    assert attempt([ticket(**columns)]) is None


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        ({"attempt": 0}, "ck_drafts_attempt"),
        ({"kind": "other"}, "ck_drafts_kind"),
        ({"proposed_action": "do_magic"}, "ck_drafts_proposed_action"),
        (
            {"review_status": "maybe", "reviewed_by": "owner", "reviewed_at": REVIEWED_AT},
            "ck_drafts_review_status",
        ),
        ({"confidence": 2}, "ck_drafts_confidence"),
        ({"review_status": "rejected"}, "ck_drafts_review_fields"),
        ({"reviewed_by": "owner"}, "ck_drafts_review_fields"),
        (
            {"review_status": "approved", "reviewed_by": "owner", "final_text": "ok"},
            "ck_drafts_review_fields",
        ),
        ({"input_tokens": -1}, "ck_drafts_non_negative_usage"),
        ({"latency_ms": -5}, "ck_drafts_non_negative_usage"),
    ],
)
def test_draft_rules(attempt: Attempt, columns: dict[str, Any], constraint: str) -> None:
    assert violated(attempt([ticket(), draft(**columns)])) == constraint


def test_draft_attempt_is_unique_per_ticket(attempt: Attempt) -> None:
    assert violated(attempt([ticket(), draft(), draft()])) == "uq_drafts_ticket_id_attempt"


def test_draft_needs_an_existing_ticket(attempt: Attempt) -> None:
    assert violated(attempt([draft(ticket_id=uuid4())])).startswith("fk_drafts")


def test_sent_draft_must_be_approved(attempt: Attempt) -> None:
    sql = "UPDATE drafts SET sent_at = now() WHERE ticket_id = $1"
    error = attempt([ticket(), draft(), (sql, (TICKET_ID,))])
    assert violated(error) == "ck_drafts_sent_was_approved"


def test_approval_needs_final_text(attempt: Attempt) -> None:
    sql = (
        "UPDATE drafts SET review_status = 'approved', reviewed_by = 'owner', "
        "reviewed_at = now() WHERE ticket_id = $1"
    )
    assert violated(attempt([ticket(), draft(), (sql, (TICKET_ID,))])) == (
        "ck_drafts_approved_has_text"
    )


def test_approved_draft_with_text_is_accepted(attempt: Attempt) -> None:
    sql = (
        "UPDATE drafts SET review_status = 'approved', reviewed_by = 'owner', "
        "reviewed_at = now(), final_text = 'ok', sent_at = now() WHERE ticket_id = $1"
    )
    assert attempt([ticket(), draft(), (sql, (TICKET_ID,))]) is None


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        ({"job_type": "mine_bitcoin"}, "ck_jobs_job_type"),
        ({"status": "paused"}, "ck_jobs_status"),
        ({"attempts": -1}, "ck_jobs_attempts"),
        ({"max_attempts": 0}, "ck_jobs_attempts"),
        ({"status": "running"}, "ck_jobs_lock_matches_status"),
    ],
)
def test_job_rules(attempt: Attempt, columns: dict[str, Any], constraint: str) -> None:
    assert violated(attempt([job(**columns)])) == constraint


def test_job_locked_while_queued_is_rejected(attempt: Attempt) -> None:
    sql = "INSERT INTO jobs (job_type, locked_at) VALUES ('process_ticket', now())"
    assert violated(attempt([(sql, ())])) == "ck_jobs_lock_matches_status"


def test_running_job_with_lock_is_accepted(attempt: Attempt) -> None:
    sql = (
        "INSERT INTO jobs (job_type, status, locked_at) VALUES ('process_ticket', 'running', now())"
    )
    assert attempt([(sql, ())]) is None


AUDIT_INSERT: Statement = ("INSERT INTO audit_log (event_type, actor) VALUES ('x', 'system')", ())


def test_audit_log_accepts_inserts(attempt: Attempt) -> None:
    assert attempt([AUDIT_INSERT]) is None


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET actor = 'someone'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
)
def test_audit_log_is_append_only(attempt: Attempt, sql: str) -> None:
    error = attempt([AUDIT_INSERT, (sql, ())])
    assert isinstance(error, asyncpg.PostgresError), error
    assert "append-only" in str(error)


def test_policy_embedding_width_and_uniqueness(attempt: Attempt) -> None:
    vector = "[" + ",".join(["0.1"] * 1536) + "]"
    sql = (
        "INSERT INTO policy_embeddings (policy_id, policy_version, passage_text, embed_model, "
        "embedding) VALUES ('RET-1.1', '1.1', 'p', 'm', $1::vector)"
    )
    assert attempt([(sql, (vector,))]) is None
    assert violated(attempt([(sql, (vector,)), (sql, (vector,))])) == (
        "uq_policy_embeddings_policy_id_version"
    )
    short = "[0.1,0.2]"
    error = attempt([(sql, (short,))])
    assert isinstance(error, asyncpg.PostgresError), error
