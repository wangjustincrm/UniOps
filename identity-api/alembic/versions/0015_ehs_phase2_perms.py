"""Permission keys for the committee, the document library and the equipment register.

Seven keys. Two of the grants are deliberate departures from "HSE and their
coordinator only":

**ehs.document.read goes to every active role**, the same way
ehs.incident.report does. Read & Sign is worthless if the people who must sign
cannot open the document, and this module's gate resolves an ungranted key to
false for everybody — so a missing grant here would not degrade the feature, it
would present a 403 to two hundred people being asked to acknowledge a policy.
Written as a SELECT over role_defs so roles added later are covered too.

**ehs.asset.inspect is separate from ehs.asset.manage.** Checking an
extinguisher and adding one to the register are different acts by different
people; the walkaround is done by supervisors and first aiders who have no
business editing the register itself.

Revision id length: alembic_version_identity.version_num is varchar(32);
"0015_ehs_phase2_perms" is 21 characters.
"""
from alembic import op

revision = "0015_ehs_phase2_perms"
down_revision = "0014_requester_is_worker"
branch_labels = None
depends_on = None

# (key, label, sort)
_KEYS = [
    ("ehs.jhsc.read",       "View JHSC Committee",          360),
    ("ehs.jhsc.manage",     "Manage JHSC Committee",        361),
    ("ehs.document.read",   "View Safety Documents",        370),
    ("ehs.document.manage", "Manage Safety Documents",      371),
    ("ehs.asset.read",      "View Safety Equipment",        380),
    ("ehs.asset.inspect",   "Inspect Safety Equipment",     381),
    ("ehs.asset.manage",    "Manage Safety Equipment",      382),
]

_GRANTS = {
    "ehs.jhsc.read":   ["ehs_manager", "ehs_coordinator", "jhsc_member",
                        "area_supervisor", "auditor"],
    "ehs.jhsc.manage": ["ehs_manager", "ehs_coordinator"],
    "ehs.document.manage": ["ehs_manager", "ehs_coordinator"],
    "ehs.asset.read":    ["ehs_manager", "ehs_coordinator", "area_supervisor",
                          "jhsc_member", "auditor"],
    "ehs.asset.inspect": ["ehs_manager", "ehs_coordinator", "area_supervisor", "first_aider"],
    "ehs.asset.manage":  ["ehs_manager", "ehs_coordinator"],
}

_EVERYONE_KEY = "ehs.document.read"


def upgrade() -> None:
    for key, label, sort in _KEYS:
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','ehs','{label}',{sort}) "
            "ON CONFLICT (key) DO NOTHING"
        )
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"SELECT code, '{_EVERYONE_KEY}' FROM role_defs WHERE is_active "
        "ON CONFLICT DO NOTHING"
    )
    for key, roles in _GRANTS.items():
        for role in roles:
            op.execute(
                "INSERT INTO role_permissions(role_code,permission_key) "
                f"VALUES ('{role}','{key}') ON CONFLICT DO NOTHING"
            )


def downgrade() -> None:
    keys = ",".join(f"'{k}'" for k, _, _ in _KEYS)
    op.execute(f"DELETE FROM role_permissions WHERE permission_key IN ({keys})")
    op.execute(f"DELETE FROM role_permission_locks WHERE permission_key IN ({keys})")
    op.execute(f"DELETE FROM permission_defs WHERE key IN ({keys})")
