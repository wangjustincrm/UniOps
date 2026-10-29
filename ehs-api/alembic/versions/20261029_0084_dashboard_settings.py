"""Dashboard colours, chart styles, and which panels a person has seen
(round 10, batch 4).

Revision ID: 20261029_0084
Revises: 20261028_0083

* ``ehs_config.dashboard_thresholds`` — the numbers HSE sets once for
  everybody: {metric: {"good": n, "bad": n}}. The days-since figure starts at
  Kirk's own example (green above 15, red below 5); that is a starting value,
  not a rule, and HSE can clear it.
* ``ehs_config.dashboard_chart_styles`` — bar, circle or body picture, per
  chart, for everybody.
* ``ehs_dashboard_layouts.known`` — the panels that existed when the person
  last saved. A panel added later is offered to them (appended) instead of
  staying hidden because their saved list predates it. Existing rows are
  stamped with the seven panels there were before this revision.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261029_0084"
down_revision = "20261028_0083"
branch_labels = None
depends_on = None

_BEFORE = ('["injuries_by_month", "near_misses", "participation", "injured_locations", '
           '"injured_departments", "body_parts", "jhsc_attendance"]')


def upgrade() -> None:
    op.add_column("ehs_config", sa.Column(
        "dashboard_thresholds", JSONB, nullable=False,
        server_default='{"days_since": {"good": 15, "bad": 5}}'))
    op.add_column("ehs_config", sa.Column(
        "dashboard_chart_styles", JSONB, nullable=False, server_default="{}"))
    op.add_column("ehs_dashboard_layouts", sa.Column(
        "known", JSONB, nullable=False, server_default="[]"))
    n = op.get_bind().exec_driver_sql(
        f"UPDATE ehs_dashboard_layouts SET known = '{_BEFORE}'::jsonb").rowcount
    print(f"0084: stamped {n} saved dashboard layout(s) with the seven earlier panels")


def downgrade() -> None:
    # Saved layouts may name panels this revision's code added. Those keys stay
    # in `panels` and the older code drops unknown keys when it reads them, so
    # nothing fails; what is lost is a preference, not a record. Said aloud
    # rather than silently.
    n = op.get_bind().exec_driver_sql(
        f"SELECT count(*) FROM ehs_dashboard_layouts WHERE NOT (panels <@ '{_BEFORE}'::jsonb)"
    ).scalar()
    if n:
        print(f"0084 downgrade: {n} saved layout(s) name panels the older code does not "
              "have; it will ignore them. Thresholds and chart styles are dropped.")
    op.drop_column("ehs_dashboard_layouts", "known")
    op.drop_column("ehs_config", "dashboard_chart_styles")
    op.drop_column("ehs_config", "dashboard_thresholds")
