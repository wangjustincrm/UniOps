"""Permission keys for work permits.

Three, not one, because three different people are involved and merging them is
the defect the permit exists to prevent. The person who needs a tank cleaned is
not the person authorised to allow entry into it; if requesting and issuing
share a key, the same person does both and the control is theatre.

`ehs.permit.request` goes to every active role — a contractor's escort, a
maintainer, a supervisor all raise these — while `ehs.permit.issue` is narrow.
Reading is wide because an open confined-space permit is something anybody
walking past has an interest in knowing about.

Revision id length: alembic_version_identity.version_num is varchar(32);
"0016_ehs_permit_perms" is 21 characters.
"""
from alembic import op

revision = "0016_ehs_permit_perms"
down_revision = "0015_ehs_phase2_perms"
branch_labels = None
depends_on = None

_KEYS = [
    ("ehs.permit.request", "Request a Work Permit", 390),
    ("ehs.permit.read",    "View Work Permits",     391),
    ("ehs.permit.issue",   "Issue and Close Work Permits", 392),
]

_GRANTS = {
    "ehs.permit.read":  ["ehs_manager", "ehs_coordinator", "area_supervisor",
                         "jhsc_member", "first_aider", "auditor"],
    # Deliberately narrow. Issuing is an authority, not a task.
    "ehs.permit.issue": ["ehs_manager", "ehs_coordinator", "area_supervisor"],
}

_EVERYONE_KEY = "ehs.permit.request"


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
