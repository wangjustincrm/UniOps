"""Add factor_combo JSONB to purchase_requests + merge prior parallel heads.

Captures the decomposition factor selection for a PR whose Budget Account has
factors configured (mirrors budget-api `budget_plan_breakdowns.factor_combo`).
Nullable — PRs against non-decomposed accounts and legacy rows leave it NULL.

This revision also acts as a **merge point**: prior to this PR the migration
graph had two unresolved heads — `r8m9n0o1p2q3` (over_budget on PR) and
`erp001_users_erp_person_code` (ERP person code on users). Both are listed as
parents below so future `alembic upgrade head` resolves to a single head.

Revision ID: s9n0o1p2q3r4
Revises: erp001_users_erp_person_code, r8m9n0o1p2q3
Create Date: 2026-05-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "s9n0o1p2q3r4"
down_revision = ("erp001_users_erp_person_code", "r8m9n0o1p2q3")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent guard: column may already exist if a prior aborted run partially
    # applied. Skip in that case so a re-run succeeds without manual DB surgery.
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='purchase_requests' "
        "AND column_name='factor_combo'"
    )).first()
    if exists:
        return
    op.add_column(
        "purchase_requests",
        sa.Column("factor_combo", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("purchase_requests", "factor_combo")
