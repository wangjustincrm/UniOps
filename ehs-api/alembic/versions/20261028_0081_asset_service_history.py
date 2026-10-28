"""Safety equipment out of service and back, with its history.

Round 10 (open box): "Failing it takes the item out of service and raises a
corrective action and should also list the item as being out of service on
the my actions for all staff to read … when the safety equipment capa has been
completed and verified, put the equipment back into service and log the date
and time and by whom on the equipment register."

- ehs_asset_status_events: each time an item goes out of service or back,
  when, by whom, why, and the corrective action behind it. Items out of
  service today get one row ("before history was kept"), so "out since" has
  an answer.
- ehs_actions.asset_id: the item a corrective action is about, whatever
  raised it (a checklist submission, or a check recorded without one, which
  is source_type 'asset').
- Checks recorded on a checklist were stored as 'fail'/'pass', where every
  other path and the out-of-service rule use 'failed'/'passed' — so a failed
  checklist never took its item out of service. Normalised.

Revision ID: 20261028_0081
Revises: 20261028_0080
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261028_0081"
down_revision = "20261028_0080"
branch_labels = None
depends_on = None

BACKFILL = "Out of service before this history was kept"
_OLD = ("'incident', 'inspection', 'jhsc', 'audit', 'observation', 'hazard', 'drill', "
        "'permit', 'manual'")


def upgrade() -> None:
    conn = op.get_bind()
    for old, new in (("fail", "failed"), ("pass", "passed")):
        n = conn.exec_driver_sql(
            f"UPDATE ehs_asset_inspections SET result = '{new}' WHERE result = '{old}'").rowcount
        print(f"0081: {n} equipment check(s) recorded as '{old}' now read '{new}'")

    op.add_column("ehs_actions", sa.Column(
        "asset_id", UUID(as_uuid=True),
        sa.ForeignKey("ehs_safety_assets.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_ehs_actions_asset_id", "ehs_actions", ["asset_id"])
    op.drop_constraint("ck_ehs_action_source_type", "ehs_actions", type_="check")
    op.create_check_constraint("ck_ehs_action_source_type", "ehs_actions",
                               f"source_type IN ({_OLD}, 'asset')")

    op.create_table(
        "ehs_asset_status_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_safety_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("by_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("by_name", sa.String(255), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("action_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_ehs_asset_status_events_asset_id", "ehs_asset_status_events",
                    ["asset_id"])
    op.execute(f"""
        INSERT INTO ehs_asset_status_events (id, asset_id, status, at, reason)
        SELECT gen_random_uuid(), id, 'out_of_service', updated_at, '{BACKFILL}'
          FROM ehs_safety_assets WHERE status = 'out_of_service'
    """)


def downgrade() -> None:
    # The history is the register's record of who put what back and when;
    # refuse to drop it while it holds more than the rows backfilled here.
    n = op.get_bind().exec_driver_sql(
        f"SELECT count(*) FROM ehs_asset_status_events WHERE reason IS DISTINCT FROM '{BACKFILL}'"
    ).scalar()
    if n:
        raise RuntimeError(f"ehs_asset_status_events holds {n} recorded change(s); "
                           "downgrading past 0081 would lose them.")
    op.drop_index("ix_ehs_asset_status_events_asset_id", table_name="ehs_asset_status_events")
    op.drop_table("ehs_asset_status_events")
    n = op.get_bind().exec_driver_sql(
        "SELECT count(*) FROM ehs_actions WHERE source_type = 'asset'").scalar()
    if n:
        raise RuntimeError(f"{n} corrective action(s) were raised from equipment checks; "
                           "downgrading past 0081 would break the source check.")
    op.drop_constraint("ck_ehs_action_source_type", "ehs_actions", type_="check")
    op.create_check_constraint("ck_ehs_action_source_type", "ehs_actions",
                               f"source_type IN ({_OLD})")
    op.drop_index("ix_ehs_actions_asset_id", table_name="ehs_actions")
    op.drop_column("ehs_actions", "asset_id")
    # The 'fail'/'pass' spelling is not restored: it was the fault.
