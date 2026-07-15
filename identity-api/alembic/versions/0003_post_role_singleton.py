"""post roles are singletons (partial unique index on user_roles)"""
from alembic import op

revision = "0003_post_role_singleton"
down_revision = "0002_authz_tables"
branch_labels = None
depends_on = None

_POSTS = "'gm','opm','vendor_manager','finance_manager','procurement_manager'"


def upgrade() -> None:
    # Backstop only — the API also checks users.role (a post held as a PRIMARY
    # role is invisible to this index). See put_user_roles.
    op.execute(
        f"CREATE UNIQUE INDEX uq_user_roles_singleton_post ON user_roles (role_code) "
        f"WHERE role_code IN ({_POSTS})")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_user_roles_singleton_post")
