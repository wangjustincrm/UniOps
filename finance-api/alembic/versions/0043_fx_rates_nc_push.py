"""FX rate module + AP push to NC (XChange).

User, 2026-10-27 (spec 2026-10-27-ap-push-to-nc-xchange-design.md):

* exchange_rates gets a provenance (`source` = boc | manual), an optional note
  and who changed it. Existing rows were all entered by hand through
  POST /bank/rates, so they are `manual`. The daily Bank of Canada fetch never
  overwrites a manual row.
* fx_fetch_runs records each fetch, so Finance -> Exchange Rates can show
  when rates were last brought in and whether that worked.
* ap_invoices gets the NC payable bill the push created (pk + number) and the
  outcome of the last push.
* nc_push_log keeps every XChange request/response for audit.

The Excel export columns (nc_exported_at / nc_export_batch_id) and the
nc_export_batches table stay: they are the history of what was exported
before the push replaced it.

Revision ID: 0043_fx_rates_nc_push
Revises: 0042_bank_recon_include_unposted
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0043_fx_rates_nc_push"
down_revision = "0042_bank_recon_include_unposted"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exchange_rates", sa.Column(
        "source", sa.String(10), nullable=False, server_default="manual"))
    op.add_column("exchange_rates", sa.Column("note", sa.String(255), nullable=True))
    op.add_column("exchange_rates", sa.Column("updated_by", UUID(as_uuid=True), nullable=True))

    op.create_table(
        "fx_fetch_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.Column("rows_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("started_by", UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_fx_fetch_runs_started_at", "fx_fetch_runs", ["started_at"])

    op.add_column("ap_invoices", sa.Column("nc_bill_pk", sa.String(25), nullable=True))
    op.add_column("ap_invoices", sa.Column("nc_bill_no", sa.String(40), nullable=True))
    op.add_column("ap_invoices", sa.Column("nc_push_status", sa.String(20), nullable=True))
    op.add_column("ap_invoices", sa.Column("nc_push_error", sa.Text(), nullable=True))
    op.add_column("ap_invoices", sa.Column("nc_pushed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ap_invoices", sa.Column("nc_pushed_by", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_ap_invoices_nc_bill_no", "ap_invoices", ["nc_bill_no"])

    op.create_table(
        "nc_push_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ap_invoice_id", UUID(as_uuid=True),
                  sa.ForeignKey("ap_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pushed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_id", sa.String(60), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("result_code", sa.String(20), nullable=True),
        sa.Column("nc_bill_pk", sa.String(25), nullable=True),
        sa.Column("nc_bill_no", sa.String(40), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("request_xml", sa.Text(), nullable=True),
        sa.Column("response_xml", sa.Text(), nullable=True),
    )
    op.create_index("ix_nc_push_log_ap_invoice_id", "nc_push_log", ["ap_invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_nc_push_log_ap_invoice_id", table_name="nc_push_log")
    op.drop_table("nc_push_log")
    op.drop_index("ix_ap_invoices_nc_bill_no", table_name="ap_invoices")
    for c in ("nc_pushed_by", "nc_pushed_at", "nc_push_error", "nc_push_status",
              "nc_bill_no", "nc_bill_pk"):
        op.drop_column("ap_invoices", c)
    op.drop_index("ix_fx_fetch_runs_started_at", table_name="fx_fetch_runs")
    op.drop_table("fx_fetch_runs")
    for c in ("updated_by", "note", "source"):
        op.drop_column("exchange_rates", c)
