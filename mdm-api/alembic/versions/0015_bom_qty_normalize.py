"""boms/bom_lines: batch-scale qty normalization (MRP phase0 CRITICAL fix)

`bom_lines.qty_per`/`qty_per_secondary` were storing NC BD_BOM_B's
NITEMNUM/NASSITEMNUM verbatim — a whole-BATCH quantity, not a per-unit-of-
parent one. The batch size lives on the header (BD_BOM.HNPARENTNUM/
HNASSPARENTNUM) and was never captured. Downstream BOM explosion multiplied
these raw batch-scaled quantities down the cascade and produced results
wrong by orders of magnitude. See app/services/nc_bom_sync/transform.py's
module docstring (PATCH 6) for the full defect writeup and real numbers.

This migration:
  1. Adds `boms.batch_output_qty` (<- raw HNPARENTNUM, nullable) purely for
     traceability — so a planner/Phase 1C can see what batch size a line's
     qty_per was normalized against.
  2. Widens `bom_lines.qty_per`/`qty_per_secondary` from Numeric(18,6) to
     Numeric(24,10) — 6 decimal places would badly round a real, legitimate
     small ratio (e.g. S0093's CP0132 line: 1/420 = 0.0023809523809...).

No data backfill here: existing boms/bom_lines rows get corrected values by
re-running POST /mdm/v1/boms/sync (idempotent upsert by nc_source_pk), the
same as any other nc_bom_sync/transform.py mapping change.

Revision ID: 0015_bom_qty_normalize
Revises: 0014_bom_line_secondary_uom
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_bom_qty_normalize"
down_revision = "0014_bom_line_secondary_uom"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("boms", sa.Column("batch_output_qty", sa.Numeric(24, 8), nullable=True))
    op.alter_column(
        "bom_lines", "qty_per",
        existing_type=sa.Numeric(18, 6), type_=sa.Numeric(24, 10), existing_nullable=False,
    )
    op.alter_column(
        "bom_lines", "qty_per_secondary",
        existing_type=sa.Numeric(18, 6), type_=sa.Numeric(24, 10), existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "bom_lines", "qty_per_secondary",
        existing_type=sa.Numeric(24, 10), type_=sa.Numeric(18, 6), existing_nullable=True,
    )
    op.alter_column(
        "bom_lines", "qty_per",
        existing_type=sa.Numeric(24, 10), type_=sa.Numeric(18, 6), existing_nullable=False,
    )
    op.drop_column("boms", "batch_output_qty")
