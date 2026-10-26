"""Job idempotency: one job per (job type, ticket, input hash).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The default gives rows inserted without a key (and any existing rows) a key of their own.
    op.add_column(
        "jobs",
        sa.Column(
            "dedupe_key", sa.Text(), nullable=False, server_default=sa.text("gen_random_uuid()::text")
        ),
    )
    op.create_unique_constraint("uq_jobs_dedupe_key", "jobs", ["dedupe_key"])


def downgrade() -> None:
    op.drop_constraint("uq_jobs_dedupe_key", "jobs", type_="unique")
    op.drop_column("jobs", "dedupe_key")
