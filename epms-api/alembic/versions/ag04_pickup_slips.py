"""house-account pickup slips, their attachments, and invoice slip links

Revision ID: ag04_pickup_slips
Revises: ag03_agreement_schedule
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag04_pickup_slips"
down_revision = "ag03_agreement_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agreement_pickup_slips",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_agreements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slip_date", sa.Date(), nullable=False),
        sa.Column("slip_ref", sa.String(64), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("picked_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("missing_slip_reason", sa.Text(), nullable=True),
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
    op.create_index("ix_agr_slip_agreement_id", "agreement_pickup_slips", ["agreement_id"])
    op.create_index("ix_agr_slip_ref", "agreement_pickup_slips", ["slip_ref"])
    op.create_index("ix_agr_slip_status", "agreement_pickup_slips", ["status"])
    op.create_index("ix_agr_slip_invoice_id", "agreement_pickup_slips", ["invoice_id"])
    # 部分唯一索引:slip_ref 为 NULL 的多行必须能共存 —— 参考号可能糊了、
    # 可能压根没有、OCR 可能抽不到,那些行之间没有可去重的依据。
    op.create_index(
        "uq_agr_slip_ref_per_agreement", "agreement_pickup_slips",
        ["agreement_id", "slip_ref"], unique=True,
        postgresql_where=sa.text("slip_ref IS NOT NULL"))

    op.create_table(
        "agreement_slip_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agreement_pickup_slips.id", ondelete="CASCADE"), nullable=False),
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
    op.create_index("ix_agr_slip_att_slip_id", "agreement_slip_attachments", ["slip_id"])

    # invoices 是共享表,但 expense-api / finance-api / approval-api 三个镜像都是
    # 显式列清单,看不见这两个新的可空列 —— 因此**没有部署顺序约束**。
    op.add_column("invoices", sa.Column("slip_ids", postgresql.JSONB(), nullable=True))
    op.add_column("invoices", sa.Column("slip_variance_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "slip_variance_reason")
    op.drop_column("invoices", "slip_ids")
    op.drop_index("ix_agr_slip_att_slip_id", table_name="agreement_slip_attachments")
    op.drop_table("agreement_slip_attachments")
    for ix in ("uq_agr_slip_ref_per_agreement", "ix_agr_slip_invoice_id",
               "ix_agr_slip_status", "ix_agr_slip_ref", "ix_agr_slip_agreement_id"):
        op.drop_index(ix, table_name="agreement_pickup_slips")
    op.drop_table("agreement_pickup_slips")
