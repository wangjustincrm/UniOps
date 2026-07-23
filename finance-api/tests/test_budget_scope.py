from app.core.budget_scope import FULL_ACCESS_PRIMARY, FULL_ACCESS_ASSIGNED


def test_role_sets_match_budget_api():
    # Pins the duplicated constants so finance-api and budget-api cannot drift.
    assert FULL_ACCESS_PRIMARY == {
        "gm", "opm", "finance_manager", "ap_clerk",
        "system_admin", "cfo", "auditor", "procurement_manager",
    }
    assert FULL_ACCESS_ASSIGNED == {
        "gm", "opm", "finance_manager", "procurement_manager", "finance_bp",
    }


import uuid
import sqlalchemy as sa
import pytest
from app.core.budget_scope import resolve_budget_scope


@pytest.mark.asyncio
async def test_director_sees_own_and_directed_departments(db_session):
    """Live production shape: primary role dept_manager + additional role
    director; departments come from approval_dept_routing, not the role."""
    uid = uuid.uuid4()
    dept_a, dept_b = uuid.uuid4(), uuid.uuid4()
    cc_a, cc_b = uuid.uuid4(), uuid.uuid4()
    for d, cc, code in ((dept_a, cc_a, "FIN-A"), (dept_b, cc_b, "FIN-B")):
        await db_session.execute(sa.text(
            "INSERT INTO cost_centers (id, code, name, department_id, is_active) "
            "VALUES (CAST(:cc AS uuid), :code, :code, CAST(:d AS uuid), true)"),
            {"cc": str(cc), "code": code, "d": str(d)})
    # `users` mirror model only carries email/full_name (see mirrors.py) — the
    # real shared table also has department_id, which the resolver reads via
    # raw SQL (same pattern as the seed_posted_jv_two_cc fixture above).
    await db_session.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS department_id uuid"))
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, full_name, department_id) "
        "VALUES (CAST(:u AS uuid), 'director@test.local', 'Director', CAST(:d AS uuid))"),
        {"u": str(uid), "d": str(dept_a)})
    await db_session.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) "
        "VALUES (CAST(:u AS uuid), 'director')"), {"u": str(uid)})
    for d in (dept_a, dept_b):
        await db_session.execute(sa.text(
            "INSERT INTO approval_dept_routing (dept_id, director_user_id) "
            "VALUES (CAST(:d AS uuid), CAST(:u AS uuid))"),
            {"d": str(d), "u": str(uid)})

    scope = await resolve_budget_scope(db_session, uid, "dept_manager")
    assert scope.full_access is False
    assert set(scope.cost_center_ids) == {cc_a, cc_b}
