"""fr03: freight shipment attachments

Revision ID: fr03_freight_shipment_att
Revises: po01_po_payment_terms
Create Date: 2026-11-11

A shipment keeps its files: the shipping label scanned on New Shipment
(kind = waybill) and any other supporting document (kind = other). The bytes
live on the File Server, as for PR/PO/GR attachments. Purely additive.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fr03_freight_shipment_att"
down_revision = "po01_po_payment_terms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "freight_shipment_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("shipment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("freight_shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False, server_default="other"),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("storage_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_freight_shipment_attachments_shipment_id",
                    "freight_shipment_attachments", ["shipment_id"])


def downgrade() -> None:
    op.drop_index("ix_freight_shipment_attachments_shipment_id",
                  table_name="freight_shipment_attachments")
    op.drop_table("freight_shipment_attachments")
