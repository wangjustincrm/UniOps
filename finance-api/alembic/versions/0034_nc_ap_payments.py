"""NC65 accounts-payable PAYMENT mirror, and the ledger-health check it enables.

Added after the payables mirror showed something the payables alone cannot
explain: NC's subledger reports 46.9M open, while billed-minus-paid across the
same population is 26.9M. Verified 2026-09-21 — 2021 milk was billed
32,641,249.87, paid 32,114,350.99, and STILL carries 13,411,519.10 as open.
The money moved; the payment was never applied against those lines.

So `money_bal` is not "what is owed" — it is a subledger flag that, for some
suppliers, nobody clears. A cash-flow forecast built on it straight would open
with a number roughly 20M too large and would never be believed again. To
separate the suppliers whose subledger is trustworthy (205 of them, tying out
to the cent) from those whose is not (72, overstated by ~19.9M), the payments
have to be mirrored too.

Revision ID: 0034_nc_ap_payments
Revises: 0033_nc_ap_mirror
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0034_nc_ap_payments"
down_revision = "0033_nc_ap_mirror"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nc_ap_payments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("nc_pk", sa.String(25), nullable=False, unique=True),
        sa.Column("bill_no", sa.String(40), nullable=False),
        sa.Column("trade_type", sa.String(30), nullable=True),
        sa.Column("bill_type", sa.String(20), nullable=True),
        sa.Column("src_syscode", sa.Integer(), nullable=True),
        sa.Column("bill_year", sa.String(4), nullable=True),
        sa.Column("bill_period", sa.String(2), nullable=True),
        sa.Column("bill_date", sa.DateTime(timezone=True), nullable=True),
        # The date the money actually left, as distinct from when the document
        # was raised. Cash-out reporting reads this one.
        sa.Column("pay_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approve_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bill_status", sa.Integer(), nullable=True),
        sa.Column("approve_status", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("money", sa.Numeric(18, 2), nullable=True),
        sa.Column("local_money", sa.Numeric(18, 2), nullable=True),
        sa.Column("invoice_no", sa.String(100), nullable=True),
        sa.Column("invoice_no_norm", sa.String(100), nullable=True),
        sa.Column("settle_flag", sa.String(10), nullable=True),
        sa.Column("scomment", sa.String(500), nullable=True),
        sa.Column("nc_ts", sa.String(19), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_nc_ap_pay_bill_no", "nc_ap_payments", ["bill_no"])
    op.create_index("ix_nc_ap_pay_date", "nc_ap_payments", ["pay_date"])
    op.create_index("ix_nc_ap_pay_invoice_norm", "nc_ap_payments", ["invoice_no_norm"])
    op.create_index("ix_nc_ap_pay_nc_ts", "nc_ap_payments", ["nc_ts"])

    op.create_table(
        "nc_ap_payment_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("payment_id", UUID(as_uuid=True),
                  sa.ForeignKey("nc_ap_payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nc_pk", sa.String(25), nullable=False, unique=True),
        sa.Column("row_no", sa.Integer(), nullable=True),
        sa.Column("bill_no", sa.String(40), nullable=False),
        sa.Column("bill_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pay_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bill_year", sa.String(4), nullable=True),
        sa.Column("bill_period", sa.String(2), nullable=True),
        # money_de is the debit against the payable — the amount paid on this
        # line. money_bal is what NC still considers unapplied on the payment.
        sa.Column("money_de", sa.Numeric(18, 2), nullable=True),
        sa.Column("money_bal", sa.Numeric(18, 2), nullable=True),
        sa.Column("local_money_de", sa.Numeric(18, 2), nullable=True),
        sa.Column("settle_money", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("subject_code", sa.String(30), nullable=True),
        sa.Column("supplier_pk", sa.String(25), nullable=True),
        sa.Column("supplier_code", sa.String(50), nullable=True),
        sa.Column("supplier_name", sa.String(200), nullable=True),
        # NC's pointer back to the document this payment came from. Kept because
        # it is the only structural link between a payment and a payable — and
        # it is exactly what is missing for the suppliers whose subledger never
        # clears.
        sa.Column("src_bill_type", sa.String(20), nullable=True),
        sa.Column("src_bill_id", sa.String(25), nullable=True),
        sa.Column("top_bill_type", sa.String(20), nullable=True),
        sa.Column("top_bill_id", sa.String(25), nullable=True),
        sa.Column("scomment", sa.String(500), nullable=True),
        sa.Column("nc_ts", sa.String(19), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_nc_ap_payline_payment", "nc_ap_payment_lines", ["payment_id"])
    op.create_index("ix_nc_ap_payline_supplier", "nc_ap_payment_lines", ["supplier_code"])
    op.create_index("ix_nc_ap_payline_src", "nc_ap_payment_lines", ["src_bill_id"])

    for col in ("payments_upserted", "payment_lines_upserted", "payment_lines_deleted"):
        op.add_column("nc_ap_sync_runs",
                      sa.Column(col, sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for col in ("payment_lines_deleted", "payment_lines_upserted", "payments_upserted"):
        op.drop_column("nc_ap_sync_runs", col)
    op.drop_index("ix_nc_ap_payline_src", table_name="nc_ap_payment_lines")
    op.drop_index("ix_nc_ap_payline_supplier", table_name="nc_ap_payment_lines")
    op.drop_index("ix_nc_ap_payline_payment", table_name="nc_ap_payment_lines")
    op.drop_table("nc_ap_payment_lines")
    op.drop_index("ix_nc_ap_pay_nc_ts", table_name="nc_ap_payments")
    op.drop_index("ix_nc_ap_pay_invoice_norm", table_name="nc_ap_payments")
    op.drop_index("ix_nc_ap_pay_date", table_name="nc_ap_payments")
    op.drop_index("ix_nc_ap_pay_bill_no", table_name="nc_ap_payments")
    op.drop_table("nc_ap_payments")
