"""authz hub tables"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_authz_tables"
down_revision = "0001_sod_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_defs",
        sa.Column("code", sa.String(50), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_table(
        "permission_defs",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("module", sa.String(20), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_code", sa.String(50),
                  sa.ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_key", sa.String(64),
                  sa.ForeignKey("permission_defs.key", ondelete="CASCADE"), primary_key=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "role_permission_locks",
        sa.Column("role_code", sa.String(50),
                  sa.ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_key", sa.String(64),
                  sa.ForeignKey("permission_defs.key", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_code", sa.String(50),
                  sa.ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("user_roles")
    op.drop_table("role_permission_locks")
    op.drop_table("role_permissions")
    op.drop_table("permission_defs")
    op.drop_table("role_defs")
