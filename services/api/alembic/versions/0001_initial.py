"""Initial schema: tickets, order_snapshots, drafts, audit_log, jobs, policy_embeddings.

Written out by hand with literal values so it never changes when the Python enums do.
Later changes go in new revisions.

Revision ID: 0001
Revises:
Create Date: 2026-10-19
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_DEFAULT = sa.text("gen_random_uuid()")
NOW = sa.text("now()")
TS = sa.TIMESTAMP(timezone=True)


def _uuid_pk(name: str) -> sa.Column[Any]:
    return sa.Column(name, postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_DEFAULT)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tickets",
        _uuid_pk("ticket_id"),
        sa.Column("external_message_id", sa.Text(), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("in_reply_to", sa.Text()),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("customer_email", sa.Text(), nullable=False),
        sa.Column("customer_name", sa.Text()),
        sa.Column("subject", sa.Text()),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("order_id_hint", sa.Text()),
        sa.Column("attachments", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("has_photo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'new'")),
        sa.Column("intent", sa.Text()),
        sa.Column("urgency", sa.Text()),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("sentiment", sa.Text()),
        sa.Column("flags", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("escalation_reason", sa.Text()),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.Column("deleted_at", TS),
        sa.UniqueConstraint("external_message_id", name="uq_tickets_external_message_id"),
        sa.CheckConstraint("channel IN ('email', 'form')", name="channel"),
        sa.CheckConstraint(
            "status IN ('new', 'classified', 'drafted', 'escalated', 'approved', 'sent', "
            "'rejected', 'failed')",
            name="status",
        ),
        sa.CheckConstraint(
            "intent IS NULL OR intent IN ('order_status', 'refund', 'return', 'damaged_item', "
            "'product_question', 'cancellation', 'address_change', 'payment', 'warranty', 'other')",
            name="intent",
        ),
        sa.CheckConstraint(
            "urgency IS NULL OR urgency IN ('low', 'normal', 'high', 'critical')", name="urgency"
        ),
        sa.CheckConstraint(
            "sentiment IS NULL OR sentiment IN ('neutral', 'negative', 'very_negative')",
            name="sentiment",
        ),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence"),
        sa.CheckConstraint(
            "(intent IS NULL) = (urgency IS NULL) AND (intent IS NULL) = (confidence IS NULL) "
            "AND (intent IS NULL) = (sentiment IS NULL)",
            name="classification_all_or_none",
        ),
        sa.CheckConstraint(
            "flags <@ ARRAY['angry', 'abusive', 'legal_threat', 'suspected_fraud', "
            "'prompt_injection_suspected', 'repeat_complaint', 'order_mismatch', "
            "'compensation_request', 'high_value']::text[]",
            name="flags",
        ),
        sa.CheckConstraint("customer_email = lower(btrim(customer_email))", name="customer_email_normalised"),
        sa.CheckConstraint("status <> 'failed' OR failure_reason IS NOT NULL", name="failed_has_reason"),
        sa.CheckConstraint(
            "status <> 'escalated' OR escalation_reason IS NOT NULL", name="escalated_has_reason"
        ),
    )
    op.create_index("ix_tickets_thread_id", "tickets", ["thread_id"])
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_tickets_received_at", "tickets", ["received_at"])
    op.create_index("ix_tickets_customer_email", "tickets", ["customer_email"])

    op.create_table(
        "order_snapshots",
        _uuid_pk("snapshot_id"),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("taken_at", TS, nullable=False, server_default=NOW),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.ticket_id"]),
    )
    op.create_index("ix_order_snapshots_ticket_id", "order_snapshots", ["ticket_id"])

    op.create_table(
        "drafts",
        _uuid_pk("draft_id"),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default=sa.text("'llm'")),
        sa.Column("suggestion_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("proposed_action", sa.Text(), nullable=False, server_default=sa.text("'none'")),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("order_snapshot_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "retrieved_passage_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("grounding_result", postgresql.JSONB()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("review_status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("final_text", sa.Text()),
        sa.Column("reviewed_by", sa.Text()),
        sa.Column("reviewed_at", TS),
        sa.Column("sent_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.ticket_id"]),
        sa.ForeignKeyConstraint(["order_snapshot_id"], ["order_snapshots.snapshot_id"]),
        sa.UniqueConstraint("ticket_id", "attempt", name="uq_drafts_ticket_id_attempt"),
        sa.CheckConstraint("attempt >= 1", name="attempt"),
        sa.CheckConstraint("kind IN ('llm', 'fallback_template')", name="kind"),
        sa.CheckConstraint(
            "proposed_action IN ('none', 'request_information', 'return_instructions_suggested', "
            "'cancellation_suggested', 'address_change_suggested', 'store_credit_suggested', "
            "'refund_suggested', 'replacement_suggested', 'escalate')",
            name="proposed_action",
        ),
        sa.CheckConstraint("review_status IN ('pending', 'approved', 'rejected')", name="review_status"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
        sa.CheckConstraint(
            "(review_status = 'pending') = (reviewed_at IS NULL) "
            "AND (review_status = 'pending') = (reviewed_by IS NULL)",
            name="review_fields",
        ),
        sa.CheckConstraint("review_status <> 'approved' OR final_text IS NOT NULL", name="approved_has_text"),
        sa.CheckConstraint("sent_at IS NULL OR review_status = 'approved'", name="sent_was_approved"),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND latency_ms >= 0", name="non_negative_usage"
        ),
    )
    op.create_index("ix_drafts_ticket_id", "drafts", ["ticket_id"])

    op.create_table(
        "audit_log",
        sa.Column("event_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True)),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", TS, nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.ticket_id"]),
    )
    op.create_index("ix_audit_log_ticket_id_occurred_at", "audit_log", ["ticket_id", "occurred_at"])
    # Append-only (FR-15): rows can be added, never changed or removed.
    op.execute(
        """
        CREATE FUNCTION audit_log_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_reject_change()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_change()"
    )

    op.create_table(
        "jobs",
        _uuid_pk("job_id"),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True)),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("run_at", TS, nullable=False, server_default=NOW),
        sa.Column("locked_at", TS),
        sa.Column("locked_by", sa.Text()),
        sa.Column("last_error", sa.Text()),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.ticket_id"]),
        sa.CheckConstraint("job_type IN ('process_ticket', 'retention_sweep')", name="job_type"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'dead')", name="status"
        ),
        sa.CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="attempts"),
        sa.CheckConstraint("(status = 'running') = (locked_at IS NOT NULL)", name="lock_matches_status"),
    )
    op.create_index("ix_jobs_status_run_at", "jobs", ["status", "run_at"])
    op.create_index("ix_jobs_ticket_id", "jobs", ["ticket_id"])

    op.create_table(
        "policy_embeddings",
        sa.Column("embedding_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("policy_id", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("passage_text", sa.Text(), nullable=False),
        sa.Column("embed_model", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("policy_id", "policy_version", name="uq_policy_embeddings_policy_id_version"),
    )


def downgrade() -> None:
    op.drop_table("policy_embeddings")
    op.drop_table("jobs")
    op.execute("DROP TRIGGER audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER audit_log_no_update_delete ON audit_log")
    op.drop_table("audit_log")
    op.execute("DROP FUNCTION audit_log_reject_change()")
    op.drop_table("drafts")
    op.drop_table("order_snapshots")
    op.drop_table("tickets")
    # The vector extension is left installed: other objects may use it.
