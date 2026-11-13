"""HSE coordinators record MOL and WSIB filings (UAT 13).

Kirk: "Allow the coord role to add WSIB and MOL reporting information on
incidents." Recording that the Ministry was told, and the Form 7 confirmation,
was ehs.statutory.manage — HSE Manager only, and the same key says a statutory
clock does not apply, which stays the manager's call. This key records the
filings and nothing else.

Revision ID: 0029_ehs_statutory_record
Revises: 0028_ehs_asset_return
"""
from alembic import op

revision = "0029_ehs_statutory_record"
down_revision = "0028_ehs_asset_return"
branch_labels = None
depends_on = None

_KEY = "ehs.statutory.record"
_ROLES = ("ehs_manager", "ehs_coordinator")


def upgrade() -> None:
    op.execute("INSERT INTO permission_defs(key,module,label,sort) VALUES "
               f"('{_KEY}','ehs','Record MOL and WSIB Filings',371) "
               "ON CONFLICT (key) DO NOTHING")
    roles = ",".join(f"'{r}'" for r in _ROLES)
    op.execute("INSERT INTO role_permissions(role_code,permission_key) "
               f"SELECT code, '{_KEY}' FROM role_defs WHERE code IN ({roles}) "
               "ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{_KEY}'")
    op.execute(f"DELETE FROM permission_defs WHERE key = '{_KEY}'")
