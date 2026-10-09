"""Access Control key for AI Financial Analysis (finance-api /cost-analysis).

Unit costs, product cost breakdowns and margins are not for everyone who can
open the AP list, so the module does not ride on view_finance. One read key;
the module has no write surface besides the sync/threshold settings, which
are system_admin only in finance-api.

Grants: system_admin (explicitly — effective_permissions() does not
short-circuit it, see 0013), finance_manager, finance_director. Each only if
role_defs has the role, so a fresh database whose roles are seeded later does
not abort on the foreign key.

Numbered 0020, not 0016: feature/ehs-safety already holds 0016–0019. Whichever
branch reaches production second re-points its first migration's
down_revision at the other's last one (0019_location_perms ↔ this file).

Revision id length: "0020_fin_analysis_perm" is 22 characters (limit 32).
"""
from alembic import op

revision = "0020_fin_analysis_perm"
down_revision = "0015_finance_director_role"
branch_labels = None
depends_on = None

_KEY = "finance.analysis.view"
_LABEL = "AI Financial Analysis"
_SORT = 117
_ROLES = ("system_admin", "finance_manager", "finance_director")


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','finance','{_LABEL}',{_SORT}) ON CONFLICT (key) DO NOTHING")
    roles = ",".join(f"'{r}'" for r in _ROLES)
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"SELECT code, '{_KEY}' FROM role_defs WHERE code IN ({roles}) "
        "ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{_KEY}'")
