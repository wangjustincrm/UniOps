"""Access zones defined in VMS Admin, each with its own managers

The six access areas were a Postgres enum (`vms_access_area`) with their rules
hard-coded in app/services/area_rules.py. They become rows in `vms_zones`
(seeded here with the same six codes, so every existing visit keeps its value)
and `vms_visits.access_area` becomes a varchar referencing `vms_zones.code`.

Approval moves from the single VMS-wide Quality Manager roster to each zone's
own managers (`vms_zone_managers`). A zone with no managers needs no approval.
The managers are seeded EMPTY on purpose (decided 2026-10-21): an admin sets
them in VMS Admin → Zones before go-live, otherwise every zone books without
approval until they do.

`zone_approver_ids` snapshots a zone's managers on each visit at submit time.
Visits still in approval are backfilled from `quality_approver_id`, so the
Quality Manager already chosen for them keeps the step.

Revision ID: 20261021_0015
Revises: 20260610_0013
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261021_0015"
down_revision = "20260610_0013"
branch_labels = None
depends_on = None

_SEED = [
    # code, name, gmp_grade, sort_order — names as the VMS UI labelled them.
    ("office", "Office", False, 10),
    ("warehouse", "Warehouse", False, 20),
    ("production_non_gmp", "Production (Non-GMP)", False, 30),
    ("production_gmp", "Production (GMP)", True, 40),
    ("laboratory", "Laboratory", True, 50),
    ("all", "Entire Plant", True, 60),
]


def upgrade() -> None:
    op.create_table(
        "vms_zones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("gmp_grade", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "vms_zone_managers",
        sa.Column("zone_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vms_zones.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    zones = sa.table(
        "vms_zones",
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("gmp_grade", sa.Boolean), sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(zones, [
        {"code": c, "name": n, "gmp_grade": g, "sort_order": o} for c, n, g, o in _SEED
    ])

    op.execute(
        "ALTER TABLE vms_visits ALTER COLUMN access_area TYPE varchar(40) "
        "USING access_area::text"
    )
    op.create_foreign_key(
        "fk_vms_visits_access_area_zone", "vms_visits", "vms_zones",
        ["access_area"], ["code"], ondelete="RESTRICT", onupdate="CASCADE",
    )
    op.execute("DROP TYPE IF EXISTS vms_access_area")

    op.add_column("vms_visits", sa.Column(
        "zone_approver_ids", postgresql.JSONB, nullable=False, server_default="[]",
    ))
    op.execute(
        "UPDATE vms_visits SET zone_approver_ids = jsonb_build_array(quality_approver_id::text) "
        "WHERE quality_approver_id IS NOT NULL "
        "AND approval_status IN ('draft', 'submitted', 'in_review', 'returned')"
    )


def downgrade() -> None:
    # Only reversible while every visit still uses one of the six seeded codes.
    op.drop_column("vms_visits", "zone_approver_ids")
    op.drop_constraint("fk_vms_visits_access_area_zone", "vms_visits", type_="foreignkey")
    postgresql.ENUM(*[c for c, *_ in _SEED], name="vms_access_area").create(op.get_bind())
    op.execute(
        "ALTER TABLE vms_visits ALTER COLUMN access_area TYPE vms_access_area "
        "USING access_area::vms_access_area"
    )
    op.drop_table("vms_zone_managers")
    op.drop_table("vms_zones")
