"""Budget plan revisions (Plan A).

Adds version-aware revision lineage to budget_plans:
  - version           INT  NOT NULL DEFAULT 1
  - parent_plan_id    UUID NULL  (FK to budget_plans.id, ON DELETE SET NULL)
  - is_current        BOOL NOT NULL DEFAULT TRUE
  - revision_notes    TEXT NULL

Replaces the (cost_center_id, fiscal_year) uniqueness with
(cost_center_id, fiscal_year, version) and adds a partial unique index
guaranteeing at most one is_current=true row per (cc, fy).

Revision ID: 20260518_0002
Revises: 20260516_0001
Create Date: 2026-05-18
"""
import sqlalchemy as sa
from alembic import op


revision = "20260518_0002"
down_revision = "20260516_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Add columns (idempotent) ──────────────────────────────────────────
    def _col_exists(name: str) -> bool:
        res = conn.execute(sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='budget_plans' AND column_name=:c"
        ), {"c": name})
        return bool(res.first())

    if not _col_exists("version"):
        op.add_column("budget_plans",
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
    if not _col_exists("parent_plan_id"):
        op.add_column("budget_plans",
            sa.Column("parent_plan_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_budget_plans_parent",
            source_table="budget_plans", referent_table="budget_plans",
            local_cols=["parent_plan_id"], remote_cols=["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_budget_plans_parent_plan_id", "budget_plans", ["parent_plan_id"],
        )
    if not _col_exists("is_current"):
        op.add_column("budget_plans",
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )
    if not _col_exists("revision_notes"):
        op.add_column("budget_plans",
            sa.Column("revision_notes", sa.Text(), nullable=True),
        )

    # Drop server_default for cleanliness so future inserts go through the ORM.
    op.alter_column("budget_plans", "version", server_default=None)
    op.alter_column("budget_plans", "is_current", server_default=None)

    # ── 2. Backfill version=1, is_current=true for any pre-existing rows ─────
    conn.execute(sa.text(
        "UPDATE budget_plans SET version=1 WHERE version IS NULL"
    ))
    conn.execute(sa.text(
        "UPDATE budget_plans SET is_current=true WHERE is_current IS NULL"
    ))

    # ── 3. Replace uniqueness (cc, fy) → (cc, fy, version) ───────────────────
    # Drop legacy unique if present.
    def _constraint_exists(name: str) -> bool:
        res = conn.execute(sa.text(
            "SELECT 1 FROM pg_constraint WHERE conname=:n"
        ), {"n": name})
        return bool(res.first())

    if _constraint_exists("uq_budget_plan_cc_year"):
        op.drop_constraint("uq_budget_plan_cc_year", "budget_plans", type_="unique")

    if not _constraint_exists("uq_budget_plan_cc_year_version"):
        op.create_unique_constraint(
            "uq_budget_plan_cc_year_version",
            "budget_plans",
            ["cost_center_id", "fiscal_year", "version"],
        )

    # Partial unique index: only one is_current=true per (cc, fy).
    def _index_exists(name: str) -> bool:
        res = conn.execute(sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname=:n"
        ), {"n": name})
        return bool(res.first())

    if not _index_exists("uq_budget_plan_current"):
        op.execute(
            "CREATE UNIQUE INDEX uq_budget_plan_current "
            "ON budget_plans (cost_center_id, fiscal_year) "
            "WHERE is_current = true"
        )

    # ── 4. CHECK constraints ─────────────────────────────────────────────────
    if not _constraint_exists("ck_plan_version_positive"):
        op.create_check_constraint(
            "ck_plan_version_positive", "budget_plans", "version >= 1",
        )


def downgrade() -> None:
    conn = op.get_bind()

    def _constraint_exists(name: str) -> bool:
        res = conn.execute(sa.text(
            "SELECT 1 FROM pg_constraint WHERE conname=:n"
        ), {"n": name})
        return bool(res.first())

    def _index_exists(name: str) -> bool:
        res = conn.execute(sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname=:n"
        ), {"n": name})
        return bool(res.first())

    if _constraint_exists("ck_plan_version_positive"):
        op.drop_constraint("ck_plan_version_positive", "budget_plans", type_="check")

    if _index_exists("uq_budget_plan_current"):
        op.drop_index("uq_budget_plan_current", table_name="budget_plans")

    if _constraint_exists("uq_budget_plan_cc_year_version"):
        op.drop_constraint("uq_budget_plan_cc_year_version", "budget_plans", type_="unique")

    if not _constraint_exists("uq_budget_plan_cc_year"):
        op.create_unique_constraint(
            "uq_budget_plan_cc_year",
            "budget_plans",
            ["cost_center_id", "fiscal_year"],
        )

    # Drop columns
    if _constraint_exists("fk_budget_plans_parent"):
        op.drop_constraint("fk_budget_plans_parent", "budget_plans", type_="foreignkey")
    if _index_exists("ix_budget_plans_parent_plan_id"):
        op.drop_index("ix_budget_plans_parent_plan_id", table_name="budget_plans")

    op.drop_column("budget_plans", "revision_notes")
    op.drop_column("budget_plans", "is_current")
    op.drop_column("budget_plans", "parent_plan_id")
    op.drop_column("budget_plans", "version")
