"""fr02: freight budget account CRM0150102 → CRM00204

Revision ID: fr02_freight_budget_crm00204
Revises: pf02_fee_link_note
Create Date: 2026-10-30

Courier freight is charged to CRM00204 (Postage & Delivery Charges) from
2026-10-30; CRM0150102 (Freight Out FTL/LTL) is truckload freight. The cost
split of every existing Freight PA moves with it, whatever the PA's status —
pa_cost_allocations is written only by Freight PAs, and every row it holds
carries the fixed freight code. NC bills already pushed keep what was sent;
those are corrected in NC.

Downgrade moves the rows back: before this revision no row held CRM00204.
"""
from alembic import op

revision = "fr02_freight_budget_crm00204"
down_revision = "pf02_fee_link_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE pa_cost_allocations SET budget_code = 'CRM00204' "
               "WHERE budget_code = 'CRM0150102'")


def downgrade() -> None:
    op.execute("UPDATE pa_cost_allocations SET budget_code = 'CRM0150102' "
               "WHERE budget_code = 'CRM00204'")
