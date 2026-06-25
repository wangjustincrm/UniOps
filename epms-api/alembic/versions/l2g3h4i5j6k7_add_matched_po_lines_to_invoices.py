"""add matched_po_line_ids and matched_reference_total to invoices

Revision ID: l2g3h4i5j6k7
Revises: k1f2g3h4i5j6
Create Date: 2026-04-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'l2g3h4i5j6k7'
down_revision: Union[str, None] = 'k1f2g3h4i5j6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IDs of the PO line items this invoice was matched against
    op.add_column(
        'invoices',
        sa.Column('matched_po_line_ids', JSONB(), nullable=True),
    )
    # The reference total used for variance calculation (sum of selected line totals)
    op.add_column(
        'invoices',
        sa.Column('matched_reference_total', sa.Numeric(15, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('invoices', 'matched_reference_total')
    op.drop_column('invoices', 'matched_po_line_ids')
