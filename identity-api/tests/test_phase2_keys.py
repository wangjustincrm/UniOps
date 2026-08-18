"""Phase-2 keys exist with defaults identical to the require_roles they replace."""
import pytest
import sqlalchemy as sa

from scripts.seed_phase2_keys import PHASE2_DEFAULTS, PHASE2_KEYS, seed_phase2_keys

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _ensure_role_defs(db_session):
    """role_permissions.role_code FKs to role_defs.code. Other test files seed
    role_defs but roll it back at teardown when they don't explicitly commit
    (e.g. test_authz_seed.py), so this file cannot assume it survives across
    the suite. Seed the roles PHASE2_DEFAULTS references, in-transaction —
    no commit needed since it's read within the same session."""
    roles = sorted({r for roles in PHASE2_DEFAULTS.values() for r in roles})
    for i, role in enumerate(roles):
        await db_session.execute(sa.text(
            "INSERT INTO role_defs (code, label, sort, is_active) "
            "VALUES (:c, :c, :s, true) ON CONFLICT (code) DO NOTHING"),
            {"c": role, "s": i})


async def test_keys_registered_with_module(db_session):
    await seed_phase2_keys(db_session)
    rows = {k: m for k, m in (await db_session.execute(sa.text(
        "SELECT key, module FROM permission_defs WHERE key = ANY(:ks)"),
        {"ks": list(PHASE2_KEYS)})).all()}
    assert set(rows) == set(PHASE2_KEYS)
    assert rows["epms.po.write"] == "epms"
    assert rows["budget.plan.write"] == "budget"
    assert rows["mdm.vendor.write"] == "mdm"


async def test_defaults_match_the_replaced_role_sets(db_session):
    await seed_phase2_keys(db_session)
    for key, roles in PHASE2_DEFAULTS.items():
        got = set((await db_session.execute(sa.text(
            "SELECT role_code FROM role_permissions WHERE permission_key = :k"),
            {"k": key})).scalars().all())
        assert got == set(roles), f"{key}: {got} != {set(roles)}"


# Keys that are NOT retired require_roles gates and therefore carry no
# "system_admin was short-circuited in" history. finance.budget.view_dept is a
# DATA-SCOPE key: system_admin holds its counterpart finance.budget.view_all
# (company-wide), and granting the department-scoped key as well would claim
# the admin is limited to their own department. uniops_authz short-circuits
# system_admin on every key regardless.
_NOT_GATE_KEYS = frozenset({"finance.budget.view_dept"})


async def test_system_admin_granted_on_every_phase2_key(db_session):
    """require_roles short-circuits system_admin, so every replaced gate admitted
    it — including _OPENING_WRITE_ROLES, whose literal tuple omits it."""
    await seed_phase2_keys(db_session)
    for key in set(PHASE2_KEYS) - _NOT_GATE_KEYS:
        n = (await db_session.execute(sa.text(
            "SELECT count(*) FROM role_permissions "
            "WHERE permission_key = :k AND role_code = 'system_admin'"),
            {"k": key})).scalar_one()
        assert n == 1, f"{key} missing system_admin"


async def test_seed_is_idempotent(db_session):
    await seed_phase2_keys(db_session)
    counts = await seed_phase2_keys(db_session)
    assert counts["granted"] == 0
