"""mg01: join the production line (fr02) and the rule-fix line (ba01)

Revision ID: mg01_fr02_ba01
Revises: ba01_budget_alert_states, fr02_freight_budget_crm00204
Create Date: 2026-11-02

Both lines start at pf02_fee_link_note: production released
fr02_freight_budget_crm00204 (courier freight → CRM00204) while dev/cassie
carried gr01 → gr02 → agr01 → ba01 (rules questions, ISS-0098/0099/0128/0146).
Nothing to do — this only gives alembic a single head again.
"""

revision = "mg01_fr02_ba01"
down_revision = ("ba01_budget_alert_states", "fr02_freight_budget_crm00204")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
