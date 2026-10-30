"""Give every ticket a unique 8-digit complaint ID.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-30
"""

import random
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOW, HIGH = 10_000_000, 99_999_999  # eight digits, never a leading zero


def upgrade() -> None:
    op.add_column("tickets", sa.Column("complaint_id", sa.BigInteger(), nullable=True))
    # Existing tickets get distinct numbers drawn together, so the backfill cannot collide.
    conn = op.get_bind()
    ids = [row[0] for row in conn.execute(sa.text("SELECT ticket_id FROM tickets"))]
    for ticket_id, number in zip(ids, random.sample(range(LOW, HIGH + 1), len(ids)), strict=True):
        conn.execute(
            sa.text("UPDATE tickets SET complaint_id = :n WHERE ticket_id = :t"),
            {"n": number, "t": ticket_id},
        )
    # New tickets draw their number in the database, so every way of inserting one gets an ID.
    op.execute(
        f"""
        CREATE FUNCTION new_complaint_id() RETURNS bigint LANGUAGE plpgsql AS $$
        DECLARE candidate bigint;
        BEGIN
            LOOP
                candidate := {LOW} + floor(random() * {HIGH - LOW + 1})::bigint;
                EXIT WHEN NOT EXISTS (SELECT 1 FROM tickets WHERE complaint_id = candidate);
            END LOOP;
            RETURN candidate;
        END $$
        """
    )
    op.execute("ALTER TABLE tickets ALTER COLUMN complaint_id SET DEFAULT new_complaint_id()")
    op.execute("ALTER TABLE tickets ALTER COLUMN complaint_id SET NOT NULL")
    op.create_unique_constraint("uq_tickets_complaint_id", "tickets", ["complaint_id"])
    # The naming convention turns "complaint_id" into ck_tickets_complaint_id.
    op.create_check_constraint("complaint_id", "tickets", f"complaint_id BETWEEN {LOW} AND {HIGH}")


def downgrade() -> None:
    op.drop_constraint("complaint_id", "tickets", type_="check")
    op.drop_constraint("uq_tickets_complaint_id", "tickets", type_="unique")
    op.drop_column("tickets", "complaint_id")
    op.execute("DROP FUNCTION new_complaint_id()")
