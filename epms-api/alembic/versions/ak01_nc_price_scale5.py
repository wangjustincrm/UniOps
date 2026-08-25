"""Widen unit_price to five decimals on PO / GR / PA lines.

NC quotes purchase prices to five decimal places and beyond; these columns were
NUMERIC(15,2), so every import silently rounded the ERP's figure on write. The
stored MONEY was never wrong — line_total holds NC's own norigtaxmny and the
header sums its norigmny — but the price printed beside it no longer multiplied
out, and nothing downstream could re-derive one from the other.

Measured over the 4,944 in-scope NC order lines in production:

    2 decimals   319 lines disagree with NC, 43,256.09 of error (worst: 2,000
                 on PO-057-2402-02, 500,000 x 0.944 read as 500,000 x 0.94)
    5 decimals     5 lines,  1.00 of error
    8 decimals     0 lines

Five is the chosen precision. Seven production lines quote to six or eight
places; they are what the residual 1.00 is.

GR lines carry the same NC prices through the same import. PA lines are copied
from the PO line when a payment application is raised (crud/pa.py), so leaving
them at two would round the same money a second time, one document later.

line_total and every header amount stay at two decimals on purpose: those are
currency amounts, and a payable figure with five decimals is not more accurate,
it is unpayable.

This only widens: no stored value changes, and NUMERIC widening rewrites no
rows. It does NOT restore precision already lost — the digits are gone from the
mirror and have to be re-read from NC. See scripts/backfill_nc_unit_price.py,
which is the other half of this change.

Revision ID: ak01_nc_price_scale5
Revises: ai01_pr_service_completion
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ak01_nc_price_scale5"
down_revision: Union[str, None] = "ai01_pr_service_completion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("po_line_items", "gr_line_items", "pa_line_items")


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table, "unit_price",
            existing_type=sa.Numeric(15, 2),
            type_=sa.Numeric(15, 5),
            existing_nullable=False,
        )


def downgrade() -> None:
    # LOSSY, unavoidably: narrowing rounds every price back to two decimals and
    # the ERP's digits are gone again. Re-run the backfill after any downgrade
    # that is later re-upgraded.
    for table in _TABLES:
        op.alter_column(
            table, "unit_price",
            existing_type=sa.Numeric(15, 5),
            type_=sa.Numeric(15, 2),
            existing_nullable=False,
        )
