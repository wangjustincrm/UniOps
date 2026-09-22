"""NC voucher header/line facts the mirror was dropping.

Header: 制单/审核/记账 names, the voucher number as a number (so it can be
range-queried), NC's voucher type name, attachment count, the 正常/错误/作废/暂存
state and the VOUCHERKIND enum.

Line: 对方科目. quantity/unit/price already had columns — the sync just never
wrote them, so no DDL is needed for those.

★ This migration only creates structure. Every new column is NULL for the
40k+ already-imported vouchers until a FULL NC sync re-runs.

Revision ID: 0038_jv_nc_header
Revises: 0037_bank_recon_v2
"""
import sqlalchemy as sa
from alembic import op

revision = "0038_jv_nc_header"
down_revision = "0037_bank_recon_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("journal_vouchers", sa.Column("nc_num", sa.Integer(), nullable=True))
    op.add_column("journal_vouchers", sa.Column("nc_prepared_name", sa.String(100), nullable=True))
    op.add_column("journal_vouchers", sa.Column("nc_checked_name", sa.String(100), nullable=True))
    op.add_column("journal_vouchers", sa.Column("nc_manager_name", sa.String(100), nullable=True))
    op.add_column("journal_vouchers", sa.Column("nc_voucher_type_name", sa.String(40), nullable=True))
    op.add_column("journal_vouchers",
                  sa.Column("nc_attachment_count", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("journal_vouchers", sa.Column("nc_voucher_state", sa.String(10), nullable=True))
    op.add_column("journal_vouchers", sa.Column("nc_voucher_kind", sa.Integer(), nullable=True))
    op.create_index("ix_journal_vouchers_nc_voucher_state", "journal_vouchers",
                    ["nc_voucher_state"])
    op.create_index("ix_journal_vouchers_nc_voucher_kind", "journal_vouchers",
                    ["nc_voucher_kind"])
    # The list filters on period + number and sorts by date; a period/number pair
    # is what NC's own 凭证号区间 query needs.
    op.create_index("ix_journal_vouchers_period_num", "journal_vouchers",
                    ["fiscal_period", "nc_num"])
    op.add_column("journal_voucher_lines",
                  sa.Column("opposite_subject", sa.String(200), nullable=True))
    # The auxiliary filter reads jv_line_dimensions by (dim_code, value_text);
    # before this change only one dim_code was ever written, so no index existed.
    op.create_index("ix_jv_line_dimensions_code_text", "jv_line_dimensions",
                    ["dim_code", "value_text"])


def downgrade() -> None:
    op.drop_index("ix_jv_line_dimensions_code_text", table_name="jv_line_dimensions")
    op.drop_column("journal_voucher_lines", "opposite_subject")
    op.drop_index("ix_journal_vouchers_period_num", table_name="journal_vouchers")
    op.drop_index("ix_journal_vouchers_nc_voucher_kind", table_name="journal_vouchers")
    op.drop_index("ix_journal_vouchers_nc_voucher_state", table_name="journal_vouchers")
    for col in ("nc_voucher_kind", "nc_voucher_state", "nc_attachment_count",
                "nc_voucher_type_name", "nc_manager_name", "nc_checked_name",
                "nc_prepared_name", "nc_num"):
        op.drop_column("journal_vouchers", col)
