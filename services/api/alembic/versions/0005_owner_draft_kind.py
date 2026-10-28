"""Let a draft be written by the owner (a reply to an escalated ticket).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE drafts DROP CONSTRAINT ck_drafts_kind")
    op.execute(
        "ALTER TABLE drafts ADD CONSTRAINT ck_drafts_kind "
        "CHECK (kind IN ('llm', 'fallback_template', 'owner'))"
    )


def downgrade() -> None:
    # The old list has no 'owner'. Such a reply was never a model draft; 'fallback_template' is
    # the nearest kind that needs no model call, so the rows survive the downgrade.
    op.execute("UPDATE drafts SET kind = 'fallback_template' WHERE kind = 'owner'")
    op.execute("ALTER TABLE drafts DROP CONSTRAINT ck_drafts_kind")
    op.execute(
        "ALTER TABLE drafts ADD CONSTRAINT ck_drafts_kind "
        "CHECK (kind IN ('llm', 'fallback_template'))"
    )
