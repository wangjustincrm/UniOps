"""gr02: vendor resolution for damaged / discrepancy goods receipts

Revision ID: gr02_damage_resolution
Revises: gr01_over_receipt
Create Date: 2026-10-30

What Procurement arranged with the vendor (return or credit) for damaged or
discrepancy lines. Recording it closes the gr_damage_report task (ISS-0099),
which used to close on Collect. All nullable; existing rows stay empty.
"""
import sqlalchemy as sa
from alembic import op

revision = "gr02_damage_resolution"
down_revision = "gr01_over_receipt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("goods_receipts", sa.Column("damage_resolution", sa.String(20), nullable=True))
    op.add_column("goods_receipts", sa.Column("damage_resolution_ref", sa.String(100), nullable=True))
    op.add_column("goods_receipts", sa.Column("damage_resolution_note", sa.Text(), nullable=True))
    op.add_column("goods_receipts", sa.Column("damage_resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("goods_receipts", sa.Column("damage_resolved_by", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("goods_receipts", "damage_resolved_by")
    op.drop_column("goods_receipts", "damage_resolved_at")
    op.drop_column("goods_receipts", "damage_resolution_note")
    op.drop_column("goods_receipts", "damage_resolution_ref")
    op.drop_column("goods_receipts", "damage_resolution")
