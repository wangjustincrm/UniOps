import uuid

import pytest
import sqlalchemy as sa

from app.core.budget_scope import PERM_VIEW_ALL, PERM_VIEW_DEPT, resolve_budget_scope


def test_permission_keys_match_budget_api():
    # Pins the duplicated module so finance-api and budget-api cannot drift.
    assert PERM_VIEW_ALL == "finance.budget.view_all"
    assert PERM_VIEW_DEPT == "finance.budget.view_dept"


async def _seed_dept_cc(db_session, dept_id, cc_id, code):
    await db_session.execute(sa.text(
        "INSERT INTO cost_centers (id, code, name, department_id, is_active) "
        "VALUES (CAST(:cc AS uuid), :code, :code, CAST(:d AS uuid), true)"),
        {"cc": str(cc_id), "code": code, "d": str(dept_id)})


async def _add_department_column(db_session):
    """`users` mirror model only carries email/full_name (see mirrors.py) — the
    real shared table also has department_id, which the resolver reads via raw
    SQL."""
    await db_session.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS department_id uuid"))


@pytest.mark.anyio
async def test_director_sees_own_and_directed_departments(db_session):
    """Live production shape: primary role dept_manager + additional role
    director; departments come from approval_dept_routing, not the role."""
    uid = uuid.uuid4()
    dept_a, dept_b = uuid.uuid4(), uuid.uuid4()
    cc_a, cc_b = uuid.uuid4(), uuid.uuid4()
    for d, cc, code in ((dept_a, cc_a, "FIN-A"), (dept_b, cc_b, "FIN-B")):
        await _seed_dept_cc(db_session, d, cc, code)
    await _add_department_column(db_session)
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


@pytest.mark.anyio
async def test_view_all_grant_gives_company_wide(db_session):
    scope = await resolve_budget_scope(db_session, uuid.uuid4(), "finance_manager")
    assert scope.full_access is True


@pytest.mark.anyio
async def test_opm_sees_departments_routed_to_the_opm(db_session):
    """gm_or_opm = 'opm' is the only link from a department to the OPM — the
    post has no user column of its own. GM-routed departments stay out."""
    uid, own, opm_dept, gm_dept = (uuid.uuid4() for _ in range(4))
    cc_own, cc_opm, cc_gm = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for d, cc, code in ((own, cc_own, "FIN-OPM-OWN"), (opm_dept, cc_opm, "FIN-OPM-A"),
                        (gm_dept, cc_gm, "FIN-GM-ONLY")):
        await _seed_dept_cc(db_session, d, cc, code)
    await _add_department_column(db_session)
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, full_name, department_id) "
        "VALUES (CAST(:u AS uuid), 'opm@test.local', 'Ops Manager', CAST(:d AS uuid))"),
        {"u": str(uid), "d": str(own)})
    await db_session.execute(sa.text(
        "INSERT INTO approval_dept_routing (dept_id, gm_or_opm) VALUES "
        "(CAST(:o AS uuid), 'opm'), (CAST(:g AS uuid), 'gm')"),
        {"o": str(opm_dept), "g": str(gm_dept)})

    scope = await resolve_budget_scope(db_session, uid, "opm")
    assert scope.full_access is False          # opm is no longer company-wide
    assert set(scope.cost_center_ids) == {cc_own, cc_opm}


@pytest.mark.anyio
async def test_no_budget_permission_fails_closed(db_session):
    """A role holding neither key sees nothing, department or not."""
    uid, dept = uuid.uuid4(), uuid.uuid4()
    await _seed_dept_cc(db_session, dept, uuid.uuid4(), "FIN-NOPERM")
    await _add_department_column(db_session)
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, full_name, department_id) "
        "VALUES (CAST(:u AS uuid), 'nobody@test.local', 'Nobody', CAST(:d AS uuid))"),
        {"u": str(uid), "d": str(dept)})

    scope = await resolve_budget_scope(db_session, uid, "warehouse_staff")
    assert scope.full_access is False
    assert scope.cost_center_ids == []
