"""approval routing tables (phase 3)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0001_approval_routing"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_dept_routing",
        sa.Column("dept_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("gm_or_opm", sa.String(3), nullable=False, server_default="gm"),
        sa.Column("director_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("supervisor_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "approval_backups",
        sa.Column("role_code", sa.String(50), primary_key=True),
        sa.Column("backup_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("approval_backups")
    op.drop_table("approval_dept_routing")
