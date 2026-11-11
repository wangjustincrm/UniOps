"""Supervisors put safety equipment back in service (round 12, FRM-04 / D11).

Kirk: "when equipment is out of service, it lists the equipment on the my
actions dashboard however the item can not be clicked on. allow the HSE and
supervisor roles to click on the item … so that they may put it back into
service." Putting an item back was ehs.asset.manage, which only HSE holds —
0015 kept it apart from inspecting on purpose. This is a narrower key for that
one act, so a supervisor can return an item without being able to edit, add or
retire equipment. Every return still asks why, and is in the item's history
under the name of whoever did it.

The plain `supervisor` role also gets ehs.asset.read: the item opens on the
equipment register, which a supervisor could not read, so the link would have
led to a refusal.

Revision ID: 0028_ehs_asset_return
Revises: 0027_capa_verify_sup_hse
"""
from alembic import op

revision = "0028_ehs_asset_return"
down_revision = "0027_capa_verify_sup_hse"
branch_labels = None
depends_on = None

_KEY = "ehs.asset.return"
_ROLES = ("ehs_manager", "ehs_coordinator", "area_supervisor", "supervisor")


def upgrade() -> None:
    op.execute("INSERT INTO permission_defs(key,module,label,sort) VALUES "
               f"('{_KEY}','ehs','Put Safety Equipment Back in Service',383) "
               "ON CONFLICT (key) DO NOTHING")
    roles = ",".join(f"'{r}'" for r in _ROLES)
    op.execute("INSERT INTO role_permissions(role_code,permission_key) "
               f"SELECT code, '{_KEY}' FROM role_defs WHERE code IN ({roles}) "
               "ON CONFLICT DO NOTHING")
    op.execute("INSERT INTO role_permissions(role_code,permission_key) "
               "SELECT code, 'ehs.asset.read' FROM role_defs WHERE code = 'supervisor' "
               "ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{_KEY}'")
    op.execute(f"DELETE FROM permission_defs WHERE key = '{_KEY}'")
    op.execute("DELETE FROM role_permissions WHERE permission_key = 'ehs.asset.read' "
               "AND role_code = 'supervisor'")
