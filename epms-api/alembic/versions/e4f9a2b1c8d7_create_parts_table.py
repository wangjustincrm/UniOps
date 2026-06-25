"""create parts table

Revision ID: e4f9a2b1c8d7
Revises: d35208b1b063
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e4f9a2b1c8d7'
down_revision: Union[str, None] = 'd35208b1b063'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "parts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("supplier", sa.String(255), nullable=False),
        sa.Column("supplier_part_no", sa.String(100), nullable=False),
        sa.Column("supplier_item_id", sa.String(100), nullable=True),
        sa.Column("unit_price", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(50), nullable=False),
        sa.Column("image_data_url", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_parts_code", "parts", ["code"], unique=True)
    op.create_index("ix_parts_category", "parts", ["category"])


def downgrade() -> None:
    op.drop_index("ix_parts_category", table_name="parts")
    op.drop_index("ix_parts_code", table_name="parts")
    op.drop_table("parts")
