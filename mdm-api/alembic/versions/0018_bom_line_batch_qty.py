"""bom_lines.qty_per_batch: keep NC's RAW batch-scale line quantity

Migration 0015/PATCH 6 correctly normalized `bom_lines.qty_per` to
per-1-unit-of-parent (`NITEMNUM / boms.batch_output_qty`) — that division is
what the BOM explosion needs. But it kept only the QUOTIENT, at
`Numeric(24,10)`, and threw the numerator away. Two consequences, both real:

  1. A planner cannot reconcile the BOM Explorer against the NC BOM screen
     without doing the multiplication in their head, and the on-screen
     `qty_per` is what they'd multiply — which is exactly what prompted the
     2026-09-03 "BOM Explorer 精度问题" report.
  2. Reconstructing `NITEMNUM` as `qty_per * batch_output_qty` is LOSSY for
     lines whose true value needs more significant digits than the 10dp
     quotient preserves. Measured on live data (2035 canonical lines):
     max absolute error 1e-7, max RELATIVE error 5.3e-6 — small, but
     visible: CS0081's CR0214 line is NITEMNUM=0.00375508 in NC and
     reconstructs as 0.0037551 (the 8th decimal is gone). Displaying that
     next to NC's own screen would re-raise the same complaint it is meant
     to answer.

So this column stores NC's `BD_BOM_B.NITEMNUM` VERBATIM, alongside the
already-stored denominator `boms.batch_output_qty` (<- HNPARENTNUM). The
pair is the exact numerator/denominator; `qty_per` stays the convenient
pre-divided quotient every existing consumer already reads. `Numeric(24,8)`
matches both the observed maximum scale in this NC instance (8 decimals,
17 of 9022 raw lines) and `boms.batch_output_qty`'s own precision.

Nullable, and NOT backfilled here: existing rows get values from the next
`POST /mdm/v1/boms/sync` (idempotent upsert by nc_source_pk), the same
no-backfill contract migration 0015 used for the same reason — the raw
value lives in NC, not in a formula this migration could apply. Until that
sync runs, `bom_explode.py` falls back to `qty_per * batch_output_qty`, so
the BOM Explorer degrades to the lossy reconstruction rather than to a
blank column.

Revision ID: 0018_bom_line_batch_qty
Revises: 0017_material_acct_group
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_bom_line_batch_qty"
down_revision = "0017_material_acct_group"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bom_lines", sa.Column("qty_per_batch", sa.Numeric(24, 8), nullable=True))


def downgrade():
    op.drop_column("bom_lines", "qty_per_batch")
