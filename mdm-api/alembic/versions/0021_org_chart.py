"""Org chart: department boxes and the reporting lines

docs/prd/EMPLOYEES.md §13. Additive; nothing is generated here, the first version
comes from Generate on the Org Chart page. Nothing touches users.supervisor_id.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0021_org_chart"
down_revision = "0020_staff_apply_department_map"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "org_units",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("department_id", UUID(as_uuid=True), unique=True),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("org_units.id", ondelete="SET NULL")),
        sa.Column("head_employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id", ondelete="SET NULL")),
        sa.Column("relation", sa.String(10), nullable=False, server_default="child"),
        sa.Column("tone", sa.String(10)),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )
    op.create_index("ix_org_units_parent_id", "org_units", ["parent_id"])

    op.create_table(
        "org_nodes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id", ondelete="CASCADE"), unique=True),
        sa.Column("name", sa.String(255)),
        sa.Column("title", sa.String(255)),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("org_nodes.id", ondelete="SET NULL")),
        sa.Column("relation", sa.String(10), nullable=False, server_default="child"),
        sa.Column("tone", sa.String(10)),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source", sa.String(10), nullable=False, server_default="manual"),
        sa.Column("review", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )
    op.create_index("ix_org_nodes_parent_id", "org_nodes", ["parent_id"])


def downgrade() -> None:
    op.drop_table("org_nodes")
    op.drop_table("org_units")
