"""Widen tasks.document_type + approval_events.document_type to VARCHAR(20).

Originally String(10) — sized for the longest doc_type known at the time
(`pa_dir` = 6 chars, `budget_plan` = 11 chars… oh wait, that's 11). Re-checking:
the prior columns were defined as String(10) but `budget_plan` is 11 chars,
which suggests the type was already pushing the limit. `vms_visit` at 9 chars
fits, but we widen to 20 now so the next doc_type contributor doesn't trip.

Per VMS S2_ARCHITECTURE_REVIEW.md F6 (W10 / S2-B).

Revision ID: t0o1p2q3r4s5
Revises: s9n0o1p2q3r4
Create Date: 2026-06-01
"""
import sqlalchemy as sa
from alembic import op

revision = "t0o1p2q3r4s5"
down_revision = "s9n0o1p2q3r4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tasks", "document_type",
        type_=sa.String(20),
        existing_type=sa.String(10),
        existing_nullable=False,
    )
    op.alter_column(
        "approval_events", "document_type",
        type_=sa.String(20),
        existing_type=sa.String(10),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "approval_events", "document_type",
        type_=sa.String(10),
        existing_type=sa.String(20),
        existing_nullable=False,
    )
    op.alter_column(
        "tasks", "document_type",
        type_=sa.String(10),
        existing_type=sa.String(20),
        existing_nullable=False,
    )
