"""Safety module roles and permission keys.

Six new roles and fourteen keys for the Safety (EHS) module.

The grant that matters most is ehs.incident.report, which goes to EVERY active
role rather than a chosen few. Safety is the first UniOps module the production
floor will use, and the single thing all of them must be able to do is report
an injury or a near miss. uniops_authz resolves every key to an explicit
true/false with no local fallback, so a key that nobody was granted denies
everyone — for this module that would mean a couple of hundred people meeting a
403 at the exact moment they are trying to report someone getting hurt. The
grant is written as a SELECT over role_defs so it covers roles added between
now and the day this runs, not a list frozen at authoring time.

system_admin is NOT granted anything here — require_permission short-circuits
it (packages/authz/uniops_authz/core.py). Granting it explicitly would be
harmless but misleading.

`worker` is the only new role that can be somebody's primary login role. JHSC
membership, first-aid certification and area supervision are things a person
holds in addition to their job, so they are additional-only (0009).

`auditor` already exists as a built-in role and is only granted to here.

Revision id length: alembic_version_identity.version_num is varchar(32);
"0013_ehs_perms" is 14 characters.
"""
from alembic import op

revision = "0013_ehs_perms"
down_revision = "0012_po_signoff_perm"
branch_labels = None
depends_on = None

# (code, label, sort, assignable_as_primary)
_ROLES = [
    ("ehs_manager",     "HSE Manager",      210, True),
    ("ehs_coordinator", "HSE Coordinator",  211, True),
    ("area_supervisor", "Area Supervisor",  212, False),
    ("jhsc_member",     "JHSC Member",      213, False),
    ("first_aider",     "First Aider",      214, False),
    ("worker",          "Worker",           215, True),
]

# (key, label, sort)
_KEYS = [
    ("ehs.incident.report",       "Report an Incident",             300),
    ("ehs.incident.read",         "View Incidents",                 301),
    ("ehs.incident.investigate",  "Investigate Incidents",          302),
    ("ehs.incident.close",        "Close Incidents",                303),
    ("ehs.incident.medical.read", "View Medical Detail",            304),
    ("ehs.action.read",           "View Corrective Actions",        310),
    ("ehs.action.write",          "Manage Corrective Actions",      311),
    ("ehs.action.verify",         "Verify Corrective Actions",      312),
    ("ehs.firstaid.write",        "Record First Aid",               320),
    ("ehs.training.read",         "View Training Records",          330),
    ("ehs.training.write",        "Manage Training Records",        331),
    ("ehs.worker.read",           "View Worker Safety Profiles",    340),
    ("ehs.worker.write",          "Manage Worker Safety Profiles",  341),
    ("ehs.settings.manage",       "Manage Safety Settings",         350),
]
# Marking a statutory obligation as filed is what stops a legal clock, so it is
# its own key rather than part of incident.close.
_KEYS.append(("ehs.statutory.manage", "Manage Statutory Filings", 351))

# Everything except ehs.incident.report, which is handled separately below.
_GRANTS = {
    "ehs.incident.read":         ["ehs_manager", "ehs_coordinator", "area_supervisor",
                                  "jhsc_member", "auditor"],
    "ehs.incident.investigate":  ["ehs_manager", "ehs_coordinator", "area_supervisor"],
    "ehs.incident.close":        ["ehs_manager"],
    # PHIPA-sensitive: injury detail and return-to-work restrictions.
    "ehs.incident.medical.read": ["ehs_manager", "first_aider"],
    "ehs.action.read":           ["ehs_manager", "ehs_coordinator", "area_supervisor",
                                  "jhsc_member", "worker", "auditor"],
    "ehs.action.write":          ["ehs_manager", "ehs_coordinator", "area_supervisor"],
    "ehs.action.verify":         ["ehs_manager", "ehs_coordinator"],
    "ehs.firstaid.write":        ["ehs_manager", "ehs_coordinator", "first_aider"],
    "ehs.training.read":         ["ehs_manager", "ehs_coordinator", "area_supervisor", "auditor"],
    "ehs.training.write":        ["ehs_manager", "ehs_coordinator"],
    "ehs.worker.read":           ["ehs_manager", "ehs_coordinator", "area_supervisor"],
    "ehs.worker.write":          ["ehs_manager", "ehs_coordinator"],
    "ehs.statutory.manage":      ["ehs_manager"],
    "ehs.settings.manage":       ["ehs_manager"],
}

_REPORT_KEY = "ehs.incident.report"


def upgrade() -> None:
    for code, label, sort, primary in _ROLES:
        op.execute(
            "INSERT INTO role_defs(code,label,sort,is_active,assignable_as_primary) "
            f"VALUES ('{code}','{label}',{sort},true,{str(primary).lower()}) "
            "ON CONFLICT (code) DO NOTHING"
        )
    for key, label, sort in _KEYS:
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','ehs','{label}',{sort}) "
            "ON CONFLICT (key) DO NOTHING"
        )
    # Reporting an incident: every active role, including ones added later.
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"SELECT code, '{_REPORT_KEY}' FROM role_defs WHERE is_active "
        "ON CONFLICT DO NOTHING"
    )
    for key, roles in _GRANTS.items():
        for role in roles:
            op.execute(
                "INSERT INTO role_permissions(role_code,permission_key) "
                f"VALUES ('{role}','{key}') ON CONFLICT DO NOTHING"
            )


def downgrade() -> None:
    # Grants first, then the keys, then the roles — and only the ones this
    # migration created. permission_defs rows are dropped here (unlike 0011 and
    # 0012) because no other seeding path knows about the ehs.* keys, so
    # nothing else can be holding a reference to them.
    keys = [k for k, _, _ in _KEYS] + [_REPORT_KEY]
    key_list = ",".join(f"'{k}'" for k in keys)
    op.execute(f"DELETE FROM role_permissions WHERE permission_key IN ({key_list})")
    op.execute(f"DELETE FROM role_permission_locks WHERE permission_key IN ({key_list})")
    op.execute(f"DELETE FROM permission_defs WHERE key IN ({key_list})")
    role_list = ",".join(f"'{c}'" for c, _, _, _ in _ROLES)
    op.execute(f"DELETE FROM user_roles WHERE role_code IN ({role_list})")
    op.execute(f"DELETE FROM role_defs WHERE code IN ({role_list})")
