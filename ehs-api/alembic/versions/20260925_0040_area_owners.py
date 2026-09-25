"""Which areas of the plant a supervisor is responsible for.

Work handed to a role rather than to a person now reaches everybody holding
that role, which for `area_supervisor` is a single plant-wide queue: an
unassigned equipment check appeared on every supervisor's list at once. What
was missing is the fact nobody had written down — which supervisor covers which
part of the plant.

An area is a node of the `locations` tree and carries its children with it:
naming somebody for "103 – Dryer" makes them responsible for Line 1 and Line 2
underneath it, because that is how people talk about the plant and because
listing every leaf would go stale the day a line is added.

The path is stored beside the id, as everywhere else here. It is the snapshot
that keeps an assignment readable after the tree is renamed, and it is what the
subtree test compares against, so the common case needs no join.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260925_0040" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260925_0040"
down_revision = "20260924_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_area_owners",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # The snapshot, for the same reason every other name here is kept: the
        # list has to stay readable after somebody leaves, and this one is
        # read by HSE when they are working out who is missing.
        sa.Column("user_name", sa.String(255), nullable=True),
        # No foreign key: `locations` is master data owned by mdm-api and every
        # reference to it in this module is a plain UUID, by convention.
        sa.Column("location_id", UUID(as_uuid=True), nullable=False),
        sa.Column("location_path", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "location_id", name="uq_ehs_area_owner"),
    )
    op.create_index("ix_ehs_area_owners_user", "ehs_area_owners", ["user_id"])
    op.create_index("ix_ehs_area_owners_location", "ehs_area_owners", ["location_id"])


def downgrade() -> None:
    op.drop_index("ix_ehs_area_owners_location", table_name="ehs_area_owners")
    op.drop_index("ix_ehs_area_owners_user", table_name="ehs_area_owners")
    op.drop_table("ehs_area_owners")
