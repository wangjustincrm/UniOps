"""multi-dim expand: promote income_expense_item to jv_lines + coa_aux_items

Revision ID: 0019_multi_dim_expand
Revises: 0018_nc_sync_runs
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0019_multi_dim_expand"
down_revision = "0018_nc_sync_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("journal_voucher_lines",
                  sa.Column("income_expense_item_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_jv_lines_ioitem", "journal_voucher_lines", ["income_expense_item_id"])
    # data backfill: KV side-table -> the new column (KV rows stay, audit value_text)
    op.execute(
        "update journal_voucher_lines l set income_expense_item_id = d.value_id "
        "from jv_line_dimensions d "
        "where d.jv_line_id = l.id and d.dim_code = 'income_expense_item' "
        "and d.value_id is not null")

    op.create_table(
        "coa_aux_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("account_code", sa.String(10), nullable=False),
        sa.Column("dim_code", sa.String(40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("account_code", "dim_code", name="uq_coa_aux_items_acct_dim"),
    )
    op.create_index("ix_coa_aux_items_account_code", "coa_aux_items", ["account_code"])


def downgrade():
    op.drop_index("ix_coa_aux_items_account_code", table_name="coa_aux_items")
    op.drop_table("coa_aux_items")
    op.drop_index("ix_jv_lines_ioitem", table_name="journal_voucher_lines")
    op.drop_column("journal_voucher_lines", "income_expense_item_id")
