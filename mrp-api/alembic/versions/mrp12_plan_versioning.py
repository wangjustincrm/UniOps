"""Production plan versioning: an explicit default plus a released_at stamp.

Additive columns, then a one-off convergence of existing data. Before this
migration `confirm-release` never cleared the previous release, so a live
database can hold several runs all claiming `status='released'` with no way
to say which one feeds `mrp_demands` -- the frozen zone had to guess with
"the most recently created released run". The upgrade makes that guess
explicit and permanent: the most recently created released run becomes the
default, and every released run in an OLDER horizon group becomes
`superseded`, which is the state the new rules assume.

The partial unique index is the real guarantee. Clearing the old default and
setting the new one is two statements, and an application-level pair is not
safe under concurrency; two runs both claiming to be in force would leave
`mrp_demands` with no defensible meaning.

Revision chain: mrp11_min_lot_week_start_frozen -> mrp12_plan_versioning.
"""
import sqlalchemy as sa
from alembic import op

revision = "mrp12_plan_versioning"
down_revision = "mrp11_min_lot_week_start_frozen"
branch_labels = None
depends_on = None

_INDEX = "uq_mrp_mps_runs_single_default"


def upgrade() -> None:
    op.add_column(
        "mrp_mps_runs",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "mrp_mps_runs",
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )

    # A release time for rows released before the column existed. `created_at`
    # is not a guess dressed up as data: the real publish time was never
    # recorded anywhere, this is the closest available value, and its only
    # use is ordering.
    op.execute("UPDATE mrp_mps_runs SET released_at = created_at WHERE status = 'released'")

    # The most recently created released run is the one in force.
    op.execute("""
        UPDATE mrp_mps_runs SET is_default = true
        WHERE id = (
            SELECT id FROM mrp_mps_runs WHERE status = 'released'
            ORDER BY created_at DESC LIMIT 1
        )
    """)

    # Every released run in an older horizon group is history: the plan group
    # only moves forward, so none of them can be made active again.
    op.execute("""
        UPDATE mrp_mps_runs SET status = 'superseded'
        WHERE status = 'released' AND horizon_start_month < (
            SELECT horizon_start_month FROM mrp_mps_runs WHERE is_default LIMIT 1
        )
    """)

    op.create_index(
        _INDEX, "mrp_mps_runs", ["is_default"], unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="mrp_mps_runs")
    op.drop_column("mrp_mps_runs", "released_at")
    op.drop_column("mrp_mps_runs", "is_default")
