"""Table definitions (SQLAlchemy Core). The single description of the schema in Python.

Migration 0001 is written out by hand so it never changes under it; tests/db checks that running
the migrations produces exactly these tables, and that every CHECK constraint here exists in the
database.
"""

from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from services.api.models.enums import (
    Channel,
    DraftKind,
    Flag,
    Intent,
    JobStatus,
    JobType,
    ProposedAction,
    ReviewStatus,
    Sentiment,
    TicketStatus,
    Urgency,
)
from services.api.models.records import EMBEDDING_DIMENSIONS

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _in(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


def _timestamp(name: str, *, nullable: bool = True, default_now: bool = False) -> Column[Any]:
    return Column(
        name,
        TIMESTAMP(timezone=True),
        nullable=nullable,
        server_default=text("now()") if default_now else None,
    )


def _uuid_pk(name: str) -> Column[Any]:
    return Column(
        name, UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


_flag_array = ", ".join(f"'{flag.value}'" for flag in Flag)

tickets = Table(
    "tickets",
    metadata,
    _uuid_pk("ticket_id"),
    Column("external_message_id", Text, nullable=False),
    Column("thread_id", UUID(as_uuid=True), nullable=False),
    Column("in_reply_to", Text),
    Column("channel", Text, nullable=False),
    _timestamp("received_at", nullable=False),
    Column("customer_email", Text, nullable=False),
    Column("customer_name", Text),
    Column("subject", Text),
    Column("body", Text, nullable=False),
    Column("order_id_hint", Text),
    Column("attachments", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("has_photo", Boolean, nullable=False, server_default=text("false")),
    Column("status", Text, nullable=False, server_default=text("'new'")),
    Column("intent", Text),
    Column("urgency", Text),
    Column("confidence", Numeric(4, 3)),
    Column("sentiment", Text),
    Column("flags", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("escalation_reason", Text),
    Column("failure_reason", Text),
    _timestamp("created_at", nullable=False, default_now=True),
    _timestamp("updated_at", nullable=False, default_now=True),
    _timestamp("deleted_at"),
    UniqueConstraint("external_message_id", name="uq_tickets_external_message_id"),
    CheckConstraint(_in("channel", Channel), name="channel"),
    CheckConstraint(_in("status", TicketStatus), name="status"),
    CheckConstraint(
        f"intent IS NULL OR {_in('intent', Intent)}",
        name="intent",
    ),
    CheckConstraint(
        f"urgency IS NULL OR {_in('urgency', Urgency)}",
        name="urgency",
    ),
    CheckConstraint(
        f"sentiment IS NULL OR {_in('sentiment', Sentiment)}",
        name="sentiment",
    ),
    CheckConstraint(
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence"
    ),
    CheckConstraint(
        "(intent IS NULL) = (urgency IS NULL) AND (intent IS NULL) = (confidence IS NULL) "
        "AND (intent IS NULL) = (sentiment IS NULL)",
        name="classification_all_or_none",
    ),
    CheckConstraint(f"flags <@ ARRAY[{_flag_array}]::text[]", name="flags"),
    CheckConstraint(
        "customer_email = lower(btrim(customer_email))", name="customer_email_normalised"
    ),
    CheckConstraint("status <> 'failed' OR failure_reason IS NOT NULL", name="failed_has_reason"),
    CheckConstraint(
        "status <> 'escalated' OR escalation_reason IS NOT NULL", name="escalated_has_reason"
    ),
    Index("ix_tickets_thread_id", "thread_id"),
    Index("ix_tickets_status", "status"),
    Index("ix_tickets_received_at", "received_at"),
    Index("ix_tickets_customer_email", "customer_email"),
)

order_snapshots = Table(
    "order_snapshots",
    metadata,
    _uuid_pk("snapshot_id"),
    Column("ticket_id", UUID(as_uuid=True), ForeignKey("tickets.ticket_id"), nullable=False),
    Column("order_id", Text, nullable=False),
    _timestamp("taken_at", nullable=False, default_now=True),
    Column("data", JSONB, nullable=False),
    Index("ix_order_snapshots_ticket_id", "ticket_id"),
)

drafts = Table(
    "drafts",
    metadata,
    _uuid_pk("draft_id"),
    Column("ticket_id", UUID(as_uuid=True), ForeignKey("tickets.ticket_id"), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("kind", Text, nullable=False, server_default=text("'llm'")),
    Column("suggestion_only", Boolean, nullable=False, server_default=text("false")),
    Column("reply_text", Text, nullable=False),
    Column("citations", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("proposed_action", Text, nullable=False, server_default=text("'none'")),
    Column("confidence", Numeric(4, 3), nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("prompt_hash", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("policy_version", Text, nullable=False),
    Column(
        "order_snapshot_id",
        UUID(as_uuid=True),
        ForeignKey("order_snapshots.snapshot_id"),
    ),
    Column(
        "retrieved_passage_ids", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    ),
    Column("grounding_result", JSONB),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("latency_ms", Integer),
    Column("review_status", Text, nullable=False, server_default=text("'pending'")),
    Column("final_text", Text),
    Column("reviewed_by", Text),
    _timestamp("reviewed_at"),
    _timestamp("sent_at"),
    _timestamp("created_at", nullable=False, default_now=True),
    UniqueConstraint("ticket_id", "attempt", name="uq_drafts_ticket_id_attempt"),
    CheckConstraint("attempt >= 1", name="attempt"),
    CheckConstraint(_in("kind", DraftKind), name="kind"),
    CheckConstraint(_in("proposed_action", ProposedAction), name="proposed_action"),
    CheckConstraint(_in("review_status", ReviewStatus), name="review_status"),
    CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
    CheckConstraint(
        "(review_status = 'pending') = (reviewed_at IS NULL) "
        "AND (review_status = 'pending') = (reviewed_by IS NULL)",
        name="review_fields",
    ),
    CheckConstraint(
        "review_status <> 'approved' OR final_text IS NOT NULL", name="approved_has_text"
    ),
    CheckConstraint("sent_at IS NULL OR review_status = 'approved'", name="sent_was_approved"),
    CheckConstraint(
        "input_tokens >= 0 AND output_tokens >= 0 AND latency_ms >= 0", name="non_negative_usage"
    ),
    Index("ix_drafts_ticket_id", "ticket_id"),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("event_id", BigInteger, primary_key=True, autoincrement=True),
    Column("ticket_id", UUID(as_uuid=True), ForeignKey("tickets.ticket_id")),
    Column("event_type", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _timestamp("occurred_at", nullable=False, default_now=True),
    Index("ix_audit_log_ticket_id_occurred_at", "ticket_id", "occurred_at"),
)

jobs = Table(
    "jobs",
    metadata,
    _uuid_pk("job_id"),
    Column("ticket_id", UUID(as_uuid=True), ForeignKey("tickets.ticket_id")),
    Column("job_type", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'queued'")),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("max_attempts", Integer, nullable=False, server_default=text("3")),
    _timestamp("run_at", nullable=False, default_now=True),
    _timestamp("locked_at"),
    Column("locked_by", Text),
    Column("last_error", Text),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    # job_type + ticket + input hash (migration 0003). One row per key, so a retry or a
    # duplicate enqueue finds the existing job instead of creating another.
    Column("dedupe_key", Text, nullable=False, server_default=text("gen_random_uuid()::text")),
    _timestamp("created_at", nullable=False, default_now=True),
    _timestamp("updated_at", nullable=False, default_now=True),
    UniqueConstraint("dedupe_key", name="uq_jobs_dedupe_key"),
    CheckConstraint(_in("job_type", JobType), name="job_type"),
    CheckConstraint(_in("status", JobStatus), name="status"),
    CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="attempts"),
    CheckConstraint("(status = 'running') = (locked_at IS NOT NULL)", name="lock_matches_status"),
    Index("ix_jobs_status_run_at", "status", "run_at"),
    Index("ix_jobs_ticket_id", "ticket_id"),
)

policy_embeddings = Table(
    "policy_embeddings",
    metadata,
    Column("embedding_id", BigInteger, primary_key=True, autoincrement=True),
    Column("policy_id", Text, nullable=False),
    Column("policy_version", Text, nullable=False),
    Column("passage_text", Text, nullable=False),
    Column("embed_model", Text, nullable=False),
    Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
    _timestamp("created_at", nullable=False, default_now=True),
    UniqueConstraint("policy_id", "policy_version", name="uq_policy_embeddings_policy_id_version"),
)

# Owner authentication state (migration 0002). There is one owner, so lockout is a single row.
auth_lockout = Table(
    "auth_lockout",
    metadata,
    Column("id", SmallInteger, primary_key=True),
    Column("consecutive_failures", Integer, nullable=False, server_default=text("0")),
    _timestamp("locked_until"),
    _timestamp("updated_at", nullable=False, default_now=True),
    CheckConstraint("id = 1", name="single_row"),
    CheckConstraint("consecutive_failures >= 0", name="failures_non_negative"),
)

auth_refresh_tokens = Table(
    "auth_refresh_tokens",
    metadata,
    Column("jti", UUID(as_uuid=True), primary_key=True),
    Column("sid", UUID(as_uuid=True), nullable=False),
    _timestamp("issued_at", nullable=False),
    _timestamp("expires_at", nullable=False),
    _timestamp("revoked_at"),
    Column("replaced_by", UUID(as_uuid=True)),
    CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
    Index("ix_auth_refresh_tokens_sid", "sid"),
    Index("ix_auth_refresh_tokens_expires_at", "expires_at"),
)
