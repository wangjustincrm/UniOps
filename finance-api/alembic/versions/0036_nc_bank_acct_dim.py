"""NC bank-account auxiliary: master mirror + jv line column + bank_accounts link

Prerequisite for Bank Reconciliation v2. Account `100201 Checking` is a single
postable account whose REQUIRED `bank_account` auxiliary is the only thing
separating RBC from Bank of China from JPMorgan — and nc_sync never decoded it
(jv_line_dimensions held exactly one dim_code, `income_expense_item`).

Structure only: this creates an empty table and two NULL columns. A `full`
nc_sync MUST follow, or every bank-scoped read returns nothing.

Revision ID: 0036_nc_bank_acct_dim
Revises: 0035_ap_bill_dismiss
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_nc_bank_acct_dim"
down_revision = "0035_ap_bill_dismiss"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nc_bank_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nc_pk", sa.String(40), nullable=False),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("acc_num", sa.String(60), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("acc_name", sa.String(255), nullable=True),
        sa.Column("bank_name", sa.String(255), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("code", name="uq_nc_bank_accounts_code"),
        sa.UniqueConstraint("nc_pk", name="uq_nc_bank_accounts_nc_pk"),
    )

    # Same shape as cost_center_id / income_expense_item_id: a denormalized id on
    # the line so expand_by_dims can group by it, PLUS a jv_line_dimensions row
    # carrying the raw NC code for re-map safety.
    op.add_column("journal_voucher_lines",
                  sa.Column("bank_account_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_journal_voucher_lines_bank_account_id",
                    "journal_voucher_lines", ["bank_account_id"])

    # Which NC bank account a UniOps bank_accounts row IS. Without it the
    # reconciliation cannot scope the book side to the statement's account.
    op.add_column("bank_accounts",
                  sa.Column("nc_bank_account_code", sa.String(60), nullable=True))
    op.create_index("ix_bank_accounts_nc_bank_account_code",
                    "bank_accounts", ["nc_bank_account_code"])


def downgrade() -> None:
    op.drop_index("ix_bank_accounts_nc_bank_account_code", table_name="bank_accounts")
    op.drop_column("bank_accounts", "nc_bank_account_code")
    op.drop_index("ix_journal_voucher_lines_bank_account_id",
                  table_name="journal_voucher_lines")
    op.drop_column("journal_voucher_lines", "bank_account_id")
    op.drop_table("nc_bank_accounts")
