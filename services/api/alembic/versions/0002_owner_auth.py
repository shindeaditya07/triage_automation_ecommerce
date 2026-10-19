"""Owner authentication state: login lockout and refresh tokens.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")
TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "auth_lockout",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_until", TS),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint("id = 1", name="single_row"),
        sa.CheckConstraint("consecutive_failures >= 0", name="failures_non_negative"),
    )
    # The row always exists, so a login attempt can lock it with SELECT ... FOR UPDATE.
    op.execute("INSERT INTO auth_lockout (id) VALUES (1)")

    op.create_table(
        "auth_refresh_tokens",
        sa.Column("jti", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issued_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
        sa.Column("replaced_by", postgresql.UUID(as_uuid=True)),
        sa.CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
    )
    op.create_index("ix_auth_refresh_tokens_sid", "auth_refresh_tokens", ["sid"])
    op.create_index("ix_auth_refresh_tokens_expires_at", "auth_refresh_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_table("auth_refresh_tokens")
    op.drop_table("auth_lockout")
