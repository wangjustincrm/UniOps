"""nc_purchase_refetch_requests — re-read one NC order regardless of the watermark

Revision ID: nc06_po_refetch_requests
Revises: pa02_pa_invoice_ids_gin
Create Date: 2026-09-08

Structure only, no backfill. A request is raised the moment an admin deletes a
mirrored PO; orders deleted BEFORE this ships still need the manual
``rewind_nc_purchase_watermark`` once.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "nc06_po_refetch_requests"
down_revision = "pa02_pa_invoice_ids_gin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nc_purchase_refetch_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("nc_source_pk", sa.String(length=50), nullable=False),
        sa.Column("po_number", sa.String(length=50), nullable=True),
        sa.Column("reason", sa.String(length=100), nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=True),
    )
    # One standing request per NC order: a re-delete of the same order must
    # re-open the existing row (ON CONFLICT), never queue a second one.
    op.create_unique_constraint(
        "uq_nc_purchase_refetch_requests_nc_source_pk",
        "nc_purchase_refetch_requests", ["nc_source_pk"])
    # The sync reads exactly one slice of this table on every run: the pending
    # ones. Partial index so the settled history never widens that read.
    op.create_index(
        "ix_nc_purchase_refetch_requests_pending",
        "nc_purchase_refetch_requests", ["nc_source_pk"],
        postgresql_where=sa.text("fulfilled_at IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_nc_purchase_refetch_requests_pending",
                  table_name="nc_purchase_refetch_requests")
    op.drop_constraint("uq_nc_purchase_refetch_requests_nc_source_pk",
                       "nc_purchase_refetch_requests", type_="unique")
    op.drop_table("nc_purchase_refetch_requests")
