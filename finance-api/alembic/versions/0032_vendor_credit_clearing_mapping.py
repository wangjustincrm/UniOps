"""seed line_role mapping for vendor_credit_clearing (Phase B Task 5 fix round 2)

An unapplied vendor credit is money the supplier owes us, so it belongs on an
asset account until the credit note itself is booked. Business decision:
account 1123 "Advance to suppliers" (existing IFRS account, no new account
created, nothing changes in NC). Without this mapping the clearing posting
line carries a NULL account_code, and app/crud/gl.py's balance-sheet /
income-statement builders skip unmapped codes entirely — the report would be
short by credit_applied on every credited payment.

Guarded two ways, following/extending the 0007_purchase_expense precedent:
  - ON CONFLICT (mapping_type, source_code) DO NOTHING — safe if this row was
    already added by hand before this migration runs.
  - WHERE EXISTS (chart_of_accounts.code = '1123') — the COA is synced from
    NC and a given environment may not have 1123 yet; in that case this
    migration must not fail the upgrade, it should simply leave the role
    unmapped (the payment path already tolerates that, per Finding 1 of the
    Task 5 review).

Revision ID: 0032_vc_clearing_map
Revises: 0031_vc_applications
Create Date: 2026-08-07
"""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0032_vc_clearing_map"
down_revision = "0031_vc_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "INSERT INTO account_mappings (id, mapping_type, source_code, account_code) "
        "SELECT :id, 'line_role', 'vendor_credit_clearing', '1123' "
        "WHERE EXISTS (SELECT 1 FROM chart_of_accounts WHERE code = '1123') "
        "ON CONFLICT (mapping_type, source_code) DO NOTHING"
    ).bindparams(id=uuid.uuid4()))


def downgrade() -> None:
    op.execute("DELETE FROM account_mappings WHERE mapping_type='line_role' "
               "AND source_code='vendor_credit_clearing'")
