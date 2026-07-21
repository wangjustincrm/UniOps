"""default invoice match tolerance to 5%

Revision ID: aa_default_match_tolerance_5
Revises: z4_drop_temp_assignments
Create Date: 2026-07-20 00:00:00.000000

The company decision is that a small over-invoice within 5% of the PO
reference should auto-match (variance still recorded) rather than raise an
exception for manual review. The column default/server_default moves from 0
(zero tolerance) to 5. This migration also bumps existing rows that are still
at the old zero-tolerance default up to 5, so already-provisioned companies
inherit the new behaviour. Rows a company has deliberately customised to some
other value are left untouched (only 0 → 5).
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'aa_default_match_tolerance_5'
down_revision: Union[str, None] = 'z4_drop_temp_assignments'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("company_config", "invoice_match_tolerance_pct", server_default="5")
    op.execute(
        "UPDATE company_config SET invoice_match_tolerance_pct = 5 "
        "WHERE invoice_match_tolerance_pct = 0"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE company_config SET invoice_match_tolerance_pct = 0 "
        "WHERE invoice_match_tolerance_pct = 5"
    )
    op.alter_column("company_config", "invoice_match_tolerance_pct", server_default="0")
