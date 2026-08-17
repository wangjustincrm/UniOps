"""The ERP already knows when a PO line is due to arrive. We never read it.

MRP's Inventory view needs "what is on order and when does it land". The
second half turned out to be missing everywhere it was looked for:
`purchase_orders.expected_delivery` is populated on **0** of the 87 open
raw-material PO lines (164 of 1,165 issued lines company-wide, not one of them
a raw material).

The date does exist. It is in NC, at LINE level, in
`NCSC.PO_ORDER_B.DPLANARRVDATE`, on **4,890 of 4,890** approved order lines —
verified against the ERP's own PO list screen (PO-001-2510-04 → 2026-02-02,
PO-010-2604-01 → 2026-05-05, and so on, matching row for row). The NC purchase
sync's `PO_ORDER_B` query selects the material, quantities and money columns
and no date at all, so the gap was ours, not the ERP's.

**Line level, not header.** NC lets each line carry its own date and real
orders do — PO-009-2603-01's two lines differ. Folding them onto the header
would silently pick one and be wrong for the other, so this is a new column on
`po_line_items` and the header field is left exactly as it is (it stays the
hand-entered date for UniOps-native POs, and the fallback when a line has no
NC date).

Nullable with no backfill and no default: NULL means "the ERP did not state
one", which is true for every UniOps-native line and must stay
distinguishable from a real date. Existing NC lines are filled by running the
sync's own `full` mode, which updates rather than inserts — that is why the
sync's UPDATE path has to carry this column too, not just its INSERT.

The revision id is shortened from the column name it describes because
`alembic_version.version_num` is varchar(32): a longer id passes every local
check and then fails at the final UPDATE, after the DDL has already run.
Transactional DDL rolls it back, so the failure is safe -- but keep ids under
32 characters.

Revision ID: ah01_po_line_planned_arrival
Revises: ag09_receipt_amounts_nullable
Create Date: 2026-08-17
"""
import sqlalchemy as sa
from alembic import op

revision = "ah01_po_line_planned_arrival"
down_revision = "ag09_receipt_amounts_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "po_line_items",
        sa.Column("planned_arrival_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("po_line_items", "planned_arrival_date")
