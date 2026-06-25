"""Budget module — initial migration.

Creates the new budget-api tables and migrates existing data from the old
per-CC budget_l1 / budget_accounts (owned previously by epms-api).

The migration is idempotent for fresh installs:
- If old tables don't exist → skip data migration, just create new tables
- If old tables exist → dedupe to shared catalog + backfill plans + ledger

Revision ID: 20260516_0001
Revises:
Create Date: 2026-05-16
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260516_0001"
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    res = conn.execute(
        sa.text("SELECT to_regclass(:n) IS NOT NULL"),
        {"n": f"public.{name}"},
    )
    return bool(res.scalar())


def _column_exists(conn, table: str, column: str) -> bool:
    res = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
    ), {"t": table, "c": column})
    return bool(res.first())


def upgrade() -> None:
    conn = op.get_bind()
    old_l1_exists = _table_exists(conn, "budget_l1")
    old_accts_exists = _table_exists(conn, "budget_accounts")

    # ── 1. If old tables don't exist, create them fresh ─────────────────────
    if not old_l1_exists:
        op.create_table(
            "budget_l1",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("code", sa.String(20), nullable=False, unique=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_index("ix_budget_l1_active", "budget_l1", ["is_active"])
    else:
        # Existing per-CC L1 — dedupe + drop cost_center_id + add new columns
        # Add new columns first (nullable to coexist with existing data)
        for col_name, col in [
            ("description", sa.Column("description", sa.Text(), nullable=True)),
            ("sort_order", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0")),
            ("created_by", sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True)),
            ("updated_by", sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True)),
        ]:
            if not _column_exists(conn, "budget_l1", col_name):
                op.add_column("budget_l1", col)

        # Dedupe: for each duplicate code, keep the earliest-created id; capture map of old->new
        conn.execute(sa.text("""
            CREATE TEMPORARY TABLE _l1_keep AS
            SELECT DISTINCT ON (code) id AS keep_id, code
            FROM budget_l1
            ORDER BY code, created_at ASC
        """))
        # Save mapping for accounts migration
        conn.execute(sa.text("""
            CREATE TEMPORARY TABLE _l1_map AS
            SELECT b.id AS old_id, k.keep_id AS new_id, b.cost_center_id
            FROM budget_l1 b
            JOIN _l1_keep k ON k.code = b.code
        """))

    if not old_accts_exists:
        op.create_table(
            "budget_accounts",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("code", sa.String(50), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("l1_id", postgresql.UUID(as_uuid=True),
                      sa.ForeignKey("budget_l1.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("decomposition_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.UniqueConstraint("code", "l1_id", name="uq_budget_account_code_l1"),
        )
        op.create_index("ix_budget_accounts_l1_id", "budget_accounts", ["l1_id"])
        op.create_index("ix_budget_accounts_active", "budget_accounts", ["is_active"])
    else:
        # Existing budget_accounts table — capture data, then add new columns and remove old ones
        for col_name, col in [
            ("description", sa.Column("description", sa.Text(), nullable=True)),
            ("decomposition_enabled",
             sa.Column("decomposition_enabled", sa.Boolean(), nullable=False, server_default=sa.false())),
            ("sort_order", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0")),
            ("created_by", sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True)),
            ("updated_by", sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True)),
        ]:
            if not _column_exists(conn, "budget_accounts", col_name):
                op.add_column("budget_accounts", col)

    # ── 2. Create the new budget-api tables ─────────────────────────────────

    op.create_table(
        "budget_account_factors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("budget_accounts.id", ondelete="RESTRICT"),
                  nullable=False, index=True),
        sa.Column("factor_code", sa.String(30), nullable=False),
        sa.Column("factor_name", sa.String(100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "factor_code", name="uq_account_factor_code"),
    )

    op.create_table(
        "budget_account_factor_values",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("factor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("budget_account_factors.id", ondelete="RESTRICT"),
                  nullable=False, index=True),
        sa.Column("value_code", sa.String(50), nullable=False),
        sa.Column("value_name", sa.String(255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("factor_id", "value_code", name="uq_factor_value_code"),
    )

    op.create_table(
        "budget_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_step_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("cost_center_id", "fiscal_year", name="uq_budget_plan_cc_year"),
        sa.CheckConstraint(
            "status IN ('draft','submitted','in_review','approved','returned','rejected','cancelled')",
            name="ck_plan_status",
        ),
    )

    op.create_table(
        "budget_plan_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("budget_plans.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("budget_accounts.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("plan_id", "account_id", "month", name="uq_plan_line"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_plan_line_month"),
    )
    op.create_index(
        "ix_plan_lines_account_month", "budget_plan_lines", ["account_id", "month"],
    )

    op.create_table(
        "budget_plan_breakdowns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("plan_line_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("budget_plan_lines.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("factor_combo", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_breakdown_combo_gin", "budget_plan_breakdowns", ["factor_combo"],
        postgresql_using="gin",
    )

    op.create_table(
        "budget_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_service", sa.String(30), nullable=False),
        sa.Column("source_doc_type", sa.String(30), nullable=False),
        sa.Column("source_doc_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False, index=True),
        sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "source_service", "source_doc_type", "source_doc_id", "operation",
            name="uq_ledger_idempotency",
        ),
        sa.CheckConstraint(
            "operation IN ('commit','release','actualize','book_expense')",
            name="ck_ledger_op",
        ),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_ledger_month"),
    )
    op.create_index(
        "ix_ledger_lookup", "budget_ledger",
        ["cost_center_id", "account_id", "fiscal_year", "month"],
    )

    op.create_table(
        "budget_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("max_factors_per_account", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "max_factors_per_account BETWEEN 1 AND 50", name="ck_max_factors_range",
        ),
    )
    # Seed default settings row
    op.execute(
        "INSERT INTO budget_settings (id, max_factors_per_account, updated_at) "
        "VALUES (gen_random_uuid(), 10, now()) "
        "ON CONFLICT DO NOTHING"
    )

    # ── 3. Data migration from old budget_accounts (if any) ─────────────────
    if old_accts_exists:
        current_year = datetime.now(timezone.utc).year

        # 3a. For each old (cost_center_id, account) — create a plan + 12 plan_lines
        conn.execute(sa.text(f"""
            INSERT INTO budget_plans (id, cost_center_id, fiscal_year, status, created_by, approved_at)
            SELECT
              gen_random_uuid(),
              m.cost_center_id,
              {current_year},
              'approved',
              '00000000-0000-0000-0000-000000000000'::uuid,
              now()
            FROM (SELECT DISTINCT cost_center_id FROM _l1_map) m
            ON CONFLICT (cost_center_id, fiscal_year) DO NOTHING
        """))

        # 3b. Dedupe accounts by (code, new_l1_id) — captures legacy id → new id mapping
        conn.execute(sa.text("""
            CREATE TEMPORARY TABLE _acct_map AS
            SELECT
              a.id AS old_id,
              first_value(a.id) OVER (PARTITION BY a.code, m.new_id ORDER BY a.created_at) AS new_id,
              m.new_id AS new_l1_id,
              m.cost_center_id,
              a.annual_budget,
              a.committed,
              a.actual_spent
            FROM budget_accounts a
            JOIN _l1_map m ON a.l1_id = m.old_id
        """))

        # 3c. Delete duplicate account rows FIRST (must precede the UPDATE
        #     otherwise the UPDATE collides on (code, l1_id) UNIQUE when two
        #     duplicates would both point to the same keeper L1).
        conn.execute(sa.text("""
            DELETE FROM budget_accounts a
            USING _acct_map m
            WHERE a.id = m.old_id AND m.old_id <> m.new_id
        """))

        # 3d. Re-point the keeper rows to the keeper L1
        conn.execute(sa.text("""
            UPDATE budget_accounts a
            SET l1_id = m.new_l1_id
            FROM _acct_map m
            WHERE a.id = m.old_id AND m.old_id = m.new_id AND a.l1_id <> m.new_l1_id
        """))

        # 3e. Generate plan_lines: each (cc, account) → 12 months, amount=annual_budget/12
        conn.execute(sa.text(f"""
            INSERT INTO budget_plan_lines (id, plan_id, account_id, month, amount)
            SELECT
              gen_random_uuid(), p.id, m.new_id, mm, ROUND(SUM(m.annual_budget) / 12.0, 2)
            FROM _acct_map m
            JOIN budget_plans p
              ON p.cost_center_id = m.cost_center_id AND p.fiscal_year = {current_year}
            CROSS JOIN generate_series(1, 12) AS mm
            WHERE m.annual_budget > 0
            GROUP BY p.id, m.new_id, mm
            ON CONFLICT (plan_id, account_id, month) DO NOTHING
        """))

        # 3f. Backfill ledger from old committed
        conn.execute(sa.text(f"""
            INSERT INTO budget_ledger
              (id, source_service, source_doc_type, source_doc_id, operation,
               cost_center_id, account_id, fiscal_year, month, amount, notes)
            SELECT
              gen_random_uuid(),
              'migration', 'legacy_commit', gen_random_uuid(), 'commit',
              m.cost_center_id, m.new_id, {current_year}, 1,
              SUM(m.committed),
              'Backfilled from legacy budget_accounts.committed'
            FROM _acct_map m
            WHERE m.committed > 0 AND m.old_id = m.new_id
            GROUP BY m.cost_center_id, m.new_id
        """))

        # 3g. Backfill ledger from old actual_spent
        conn.execute(sa.text(f"""
            INSERT INTO budget_ledger
              (id, source_service, source_doc_type, source_doc_id, operation,
               cost_center_id, account_id, fiscal_year, month, amount, notes)
            SELECT
              gen_random_uuid(),
              'migration', 'legacy_actual', gen_random_uuid(), 'book_expense',
              m.cost_center_id, m.new_id, {current_year}, 1,
              SUM(m.actual_spent),
              'Backfilled from legacy budget_accounts.actual_spent'
            FROM _acct_map m
            WHERE m.actual_spent > 0 AND m.old_id = m.new_id
            GROUP BY m.cost_center_id, m.new_id
        """))

        # 3h. Drop old columns from budget_accounts
        for col in ("annual_budget", "committed", "actual_spent"):
            if _column_exists(conn, "budget_accounts", col):
                op.drop_column("budget_accounts", col)

    if old_l1_exists:
        # 3i. Dedupe L1: delete non-keeper rows; then drop cost_center_id column
        conn.execute(sa.text("""
            DELETE FROM budget_l1 b
            USING _l1_map m
            WHERE b.id = m.old_id AND m.old_id <> m.new_id
        """))
        if _column_exists(conn, "budget_l1", "cost_center_id"):
            # Drop FK constraint first if it exists
            conn.execute(sa.text("""
                ALTER TABLE budget_l1
                DROP CONSTRAINT IF EXISTS budget_l1_cost_center_id_fkey
            """))
            op.drop_column("budget_l1", "cost_center_id")

        # Add unique index on code now that duplicates are gone
        conn.execute(sa.text("""
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE tablename='budget_l1' AND indexname='ix_budget_l1_code'
              ) THEN
                CREATE UNIQUE INDEX ix_budget_l1_code ON budget_l1(code);
              END IF;
            END $$;
        """))


def downgrade() -> None:
    """Downgrade is destructive — drops all budget-api tables. Old tables (if migrated)
    cannot be restored to their pre-migration shape without backup. Use with care."""
    op.drop_table("budget_settings")
    op.drop_index("ix_ledger_lookup", table_name="budget_ledger")
    op.drop_table("budget_ledger")
    op.drop_index("ix_breakdown_combo_gin", table_name="budget_plan_breakdowns")
    op.drop_table("budget_plan_breakdowns")
    op.drop_index("ix_plan_lines_account_month", table_name="budget_plan_lines")
    op.drop_table("budget_plan_lines")
    op.drop_table("budget_plans")
    op.drop_table("budget_account_factor_values")
    op.drop_table("budget_account_factors")
    # NOTE: We do NOT drop budget_accounts / budget_l1 here — they predate this migration.
    # New columns added by this migration are left in place.
