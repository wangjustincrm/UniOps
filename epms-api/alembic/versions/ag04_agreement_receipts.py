"""Agreement receipts — the evidence a house-account invoice reconciles against.

Supersedes the pickup-slip shape this branch originally shipped. Receipts are
typed (counter_slip | delivery | service) because a house account is not
necessarily a counter-pickup account: it may receive deliveries or services.
Hardcoding "pickup slip" would have made this feature usable by exactly one
vendor.

Deliberately NOT reusing goods_receipts: its po_id is NOT NULL, its line items
point at po_line_id, and its acknowledgement flow assumes a PR requester.
Agreements have no PO, so widening that table would open a hole in a document
that is already live in production.

Folds in what would have shipped as a follow-up ag05: the partial unique
index on (agreement_id, receipt_ref) excludes retired rows (voided/rejected)
from day one, so a mis-keyed receipt that gets voided doesn't burn its
reference for the life of the agreement — re-recording the same paper
document has to be accepted. ⚠️ Kept byte-for-byte in sync with
app/models/agreement_receipt.py::__table_args__: the test DB is built by
Base.metadata.create_all and never runs this migration, so a predicate that
lives only here is a predicate no test can see (this branch already shipped
one such invisible constraint once).

Revision ID: ag04_agreement_receipts
Revises: ag03_agreement_schedule
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag04_agreement_receipts"
down_revision = "ag03_agreement_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agreement_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_agreements.id", ondelete="CASCADE"), nullable=False),
        # counter_slip | delivery | service
        sa.Column("receipt_type", sa.String(20), nullable=False, server_default="counter_slip"),
        sa.Column("receipt_date", sa.Date(), nullable=False),
        sa.Column("receipt_ref", sa.String(64), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("received_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("missing_receipt_reason", sa.Text(), nullable=True),
        sa.Column("ap_reviewed_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("ap_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_agr_receipt_agreement_id", "agreement_receipts", ["agreement_id"])
    op.create_index("ix_agr_receipt_type", "agreement_receipts", ["receipt_type"])
    op.create_index("ix_agr_receipt_ref", "agreement_receipts", ["receipt_ref"])
    op.create_index("ix_agr_receipt_status", "agreement_receipts", ["status"])
    op.create_index("ix_agr_receipt_invoice_id", "agreement_receipts", ["invoice_id"])
    # 部分唯一索引:退休行(voided/rejected)不参与去重,活跃行之间才互斥。见
    # 文件头注释与 app/models/agreement_receipt.py 的对应说明。
    op.create_index(
        "uq_agr_receipt_ref_per_agreement", "agreement_receipts",
        ["agreement_id", "receipt_ref"], unique=True,
        postgresql_where=sa.text(
            "receipt_ref IS NOT NULL AND status NOT IN ('voided', 'rejected')"))

    op.create_table(
        "agreement_receipt_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agreement_receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False,
                  server_default="application/octet-stream"),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("file_data", sa.LargeBinary(), nullable=True),
        sa.Column("storage_key", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_agr_receipt_att_receipt_id", "agreement_receipt_attachments", ["receipt_id"])

    # invoices 是共享表,但 expense-api / finance-api / approval-api 三个镜像都是
    # 显式列清单,看不见这两个新的可空列 —— 因此**没有部署顺序约束**。
    op.add_column("invoices", sa.Column("receipt_ids", postgresql.JSONB(), nullable=True))
    op.add_column("invoices", sa.Column("receipt_variance_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "receipt_variance_reason")
    op.drop_column("invoices", "receipt_ids")
    op.drop_index("ix_agr_receipt_att_receipt_id", table_name="agreement_receipt_attachments")
    op.drop_table("agreement_receipt_attachments")
    for ix in ("uq_agr_receipt_ref_per_agreement", "ix_agr_receipt_invoice_id",
               "ix_agr_receipt_status", "ix_agr_receipt_ref", "ix_agr_receipt_type",
               "ix_agr_receipt_agreement_id"):
        op.drop_index(ix, table_name="agreement_receipts")
    op.drop_table("agreement_receipts")
