"""agreement payment schedule, attachments, recurrence columns

Revision ID: ag03_agreement_schedule
Revises: ag02_agreement_links
Create Date: 2026-08-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag03_agreement_schedule"
down_revision = "ag02_agreement_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agreement_payment_schedule",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_agreements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schedule_type", sa.String(20), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("expected_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("expected_date", sa.Date(), nullable=True),
        sa.Column("expected_timing", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("period_label", sa.String(20), nullable=True),
        sa.Column("tolerance_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("overdue_after_days", sa.Integer(), nullable=True),
        sa.Column("milestone_name", sa.String(255), nullable=True),
        sa.Column("amount_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("trigger_condition", sa.Text(), nullable=True),
        sa.Column("accepted_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_agr_sched_agreement_id", "agreement_payment_schedule", ["agreement_id"])
    op.create_index("ix_agr_sched_type", "agreement_payment_schedule", ["schedule_type"])
    op.create_index("ix_agr_sched_status", "agreement_payment_schedule", ["status"])

    op.create_table(
        "agreement_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_agreements.id", ondelete="CASCADE"), nullable=False),
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
    op.create_index("ix_agr_att_agreement_id", "agreement_attachments", ["agreement_id"])

    op.add_column("purchase_agreements",
                  sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_purchase_agreements_cost_center_id",
                    "purchase_agreements", ["cost_center_id"])
    op.add_column("purchase_agreements", sa.Column("recurring_type", sa.String(10), nullable=True))
    op.add_column("purchase_agreements", sa.Column("expected_invoice_day", sa.Integer(), nullable=True))
    op.add_column("purchase_agreements", sa.Column("anchor_month", sa.Integer(), nullable=True))
    op.add_column("purchase_agreements",
                  sa.Column("expected_amount_per_period", sa.Numeric(15, 2), nullable=True))
    op.add_column("purchase_agreements", sa.Column("tolerance_pct", sa.Numeric(5, 2), nullable=True))
    op.add_column("purchase_agreements",
                  sa.Column("overdue_after_days", sa.Integer(), nullable=True, server_default="7"))

    # invoices 是共享表,但 expense-api / finance-api / approval-api 三个镜像都是
    # 显式列清单,看不见这个新列 —— 因此本迁移**没有部署顺序约束**(不同于 ag02 的
    # payment_applications.agreement_id,那次镜像声明了该列)。
    op.add_column("invoices", sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=True))

    # Whole-branch review finding: server_default="7" on overdue_after_days
    # above lands on EVERY pre-existing row at the moment this migration
    # runs, including every 1A house_account — not just the recurring
    # agreements the column is meant for. crud.agreement.update() 409s any
    # PATCH that carries a non-NULL overdue_after_days on a non-recurring
    # agreement ("only apply to a recurring agreement", validate_recurrence),
    # so without this backfill every pre-existing house_account becomes
    # permanently un-editable. create() already nulls it out for new
    # non-recurring agreements going forward (see its comment) — this is the
    # matching one-time cleanup for rows that already existed when this
    # migration ran.
    op.execute(
        "UPDATE purchase_agreements SET overdue_after_days = NULL "
        "WHERE agreement_type <> 'recurring'"
    )


def downgrade() -> None:
    op.drop_column("invoices", "schedule_id")
    for col in ("overdue_after_days", "tolerance_pct", "expected_amount_per_period",
                "anchor_month", "expected_invoice_day", "recurring_type"):
        op.drop_column("purchase_agreements", col)
    op.drop_index("ix_purchase_agreements_cost_center_id", table_name="purchase_agreements")
    op.drop_column("purchase_agreements", "cost_center_id")
    op.drop_index("ix_agr_att_agreement_id", table_name="agreement_attachments")
    op.drop_table("agreement_attachments")
    for ix in ("ix_agr_sched_status", "ix_agr_sched_type", "ix_agr_sched_agreement_id"):
        op.drop_index(ix, table_name="agreement_payment_schedule")
    op.drop_table("agreement_payment_schedule")
