"""Add approval_status + visit_title columns to vms_visits (W10 / S2-B).

Per S2_ARCHITECTURE_REVIEW.md decision #1 (F1) and #2 (F3):

  * `approval_status: VARCHAR(20)` — engine state (draft/submitted/in_review/
    approved/returned/rejected/cancelled) lives here, separately from the
    user-facing `VisitStatus` enum that drives VMS UX. The approval engine
    points at this column via `_DOC_META["vms_visit"]["status_attr"]`.

  * `visit_title: VARCHAR(255)` — populated by vms-api at submit time with
    "VMS Visit — {first} {last} ({company})". approval-api reads it via
    `meta["number_attr"]` so the Portal task inbox shows meaningful titles
    without a join.

Both columns default to safe initial values:
  - `approval_status` NULL (engine hasn't touched the row)
  - `visit_title` empty string (vms-api sets it on submit; for visits that
    never go to approval, it stays empty)

Revision ID: 20260601_0003
Revises: 20260528_0002
Create Date: 2026-06-01
"""
import sqlalchemy as sa
from alembic import op

revision = "20260601_0003"
down_revision = "20260528_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vms_visits",
        sa.Column("approval_status", sa.String(20), nullable=True),
    )
    op.add_column(
        "vms_visits",
        sa.Column(
            "visit_title",
            sa.String(255),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("vms_visits", "visit_title")
    op.drop_column("vms_visits", "approval_status")
