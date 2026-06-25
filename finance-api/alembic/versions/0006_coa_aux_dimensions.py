"""chart_of_accounts.aux_dimensions — 辅助核算项 (Phase a A1.5)

Per-account declaration of which posting dimensions make the account
specific (NC-style 辅助核算): partner / cost_center / item / lot /
warehouse / project / channel — exactly the posting_lines dimension
columns from B1.7. Stored as a JSONB string array; enforcement starts
as guidance (UI + validation) and hardens at GL posting (Phase 3).

Revision ID: 0006_coa_aux
Revises: 0005_coa
Create Date: 2026-06-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_coa_aux"
down_revision = "0005_coa"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chart_of_accounts",
                  sa.Column("aux_dimensions", JSONB, nullable=False, server_default="[]"))
    # sensible defaults on the seed: AP carries partner; bank none; ITC none
    op.execute("""UPDATE chart_of_accounts SET aux_dimensions = '["partner"]'::jsonb
                  WHERE code IN ('2000','1100','1310')""")
    op.execute("""UPDATE chart_of_accounts SET aux_dimensions = '["cost_center"]'::jsonb
                  WHERE account_type = 'expense'""")
    op.execute("""UPDATE chart_of_accounts SET aux_dimensions = '["item","warehouse","lot"]'::jsonb
                  WHERE code LIKE '12%' AND is_postable""")


def downgrade():
    op.drop_column("chart_of_accounts", "aux_dimensions")
