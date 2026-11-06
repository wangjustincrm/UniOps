"""Which GL vouchers each inventory / cost document generated — posted or not.

User, 2026-10-09: every voucher taking part in a cost adjustment must be shown,
including the ones NC has not posted (tallied) yet. On 2026-10-09 all 16 of
September's cost vouchers were unposted — among them 00000729, which carries
IA2026092900003299 (-46,588.85), the tail dump onto CS0080. A posted-only view
would have hidden the month's largest adjustment.

The link is NC's accounting platform (FIP_RELATION): src_freedef1 is the
document number, des_relationid the voucher. Scope: documents of the IA and CM
systems (I4/I5/I6/I7/IA/I9 …, CM09 cost carry-forward) from 2026-01-01.
Purchase receipts and settlements are NOT linked this way in 2026 — milk is
settled through hand-made accruals — so the settlement side stays document-
level only; the module says so instead of guessing.

voucher_status uses nc_sync's own rules (_tallied / _voucher_state) so
"unposted" here means exactly what it means on the Budget pages:
posted | unposted | discarded | tempsave | error | deleted.

Revision ID: 0041_nc_cost_vouchers
Revises: 0040_nc_cost_mirror
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0041_nc_cost_vouchers"
down_revision = "0040_nc_cost_mirror"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nc_cost_vouchers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("nc_pk", sa.String(25), nullable=False, unique=True),   # FIP_RELATION row
        sa.Column("src_system", sa.String(10), nullable=True),
        sa.Column("src_bill_type", sa.String(20), nullable=True),
        sa.Column("bill_code", sa.String(60), nullable=True),
        sa.Column("src_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("voucher_pk", sa.String(25), nullable=True),
        sa.Column("voucher_no", sa.String(20), nullable=True),
        sa.Column("voucher_period", sa.String(7), nullable=True),
        sa.Column("voucher_date", sa.Date(), nullable=True),
        sa.Column("explanation", sa.String(500), nullable=True),
        sa.Column("voucher_status", sa.String(12), nullable=False),
        sa.Column("dr", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("nc_ts", sa.String(19), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_nc_cost_vouchers_bill", "nc_cost_vouchers", ["bill_code"])
    op.create_index("ix_nc_cost_vouchers_voucher", "nc_cost_vouchers", ["voucher_pk"])


def downgrade() -> None:
    op.drop_table("nc_cost_vouchers")
