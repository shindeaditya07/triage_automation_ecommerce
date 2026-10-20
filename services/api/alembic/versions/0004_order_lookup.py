"""Record what the order lookup found on the ticket.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Null means the lookup has not run yet. No default, so existing tickets stay null.
    op.add_column("tickets", sa.Column("order_lookup", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "order_lookup")
