import uuid
import pytest
import sqlalchemy as sa
from app.core.budget_scope import (
    FULL_ACCESS_PRIMARY, FULL_ACCESS_ASSIGNED,
    BudgetScope, resolve_budget_scope, scoped_cc_ids,
)


def test_role_sets_are_pinned():
    # Guards against silent drift vs finance-api's copy and the frontend.
    assert FULL_ACCESS_PRIMARY == {
        "gm", "opm", "finance_manager", "ap_clerk",
        "system_admin", "cfo", "auditor", "procurement_manager",
    }
    assert FULL_ACCESS_ASSIGNED == {
        "gm", "opm", "finance_manager", "procurement_manager", "finance_bp",
    }


def test_scoped_cc_ids_logic():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    full = BudgetScope(full_access=True, cost_center_ids=[])
    dept = BudgetScope(full_access=False, cost_center_ids=[a, b])
    empty = BudgetScope(full_access=False, cost_center_ids=[])

    # full access: unchanged (None = aggregate all, or the single requested CC)
    assert scoped_cc_ids(full, None) is None
    assert scoped_cc_ids(full, a) == [a]
    # dept viewer, no request -> whole department
    assert scoped_cc_ids(dept, None) == [a, b]
    # dept viewer, in-scope CC -> that CC
    assert scoped_cc_ids(dept, a) == [a]
    # dept viewer, out-of-scope CC -> clamp to department (NOT 403)
    assert scoped_cc_ids(dept, c) == [a, b]
    # no resolvable scope -> fail-closed empty
    assert scoped_cc_ids(empty, None) == []
    assert scoped_cc_ids(empty, a) == []


@pytest.mark.asyncio
async def test_resolve_primary_full_access(db_session):
    uid = uuid.uuid4()
    scope = await resolve_budget_scope(db_session, uid, "finance_manager")
    assert scope.full_access is True


@pytest.mark.asyncio
async def test_resolve_assigned_role_full_access(db_session):
    # No FULL_ACCESS_PRIMARY primary role, but an assigned role in
    # FULL_ACCESS_ASSIGNED (e.g. finance_bp) grants full access too.
    uid = uuid.uuid4()
    await db_session.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (CAST(:uid AS uuid), 'finance_bp')"
        ),
        {"uid": str(uid)},
    )
    await db_session.commit()
    scope = await resolve_budget_scope(db_session, uid, "requester")
    assert scope.full_access is True


@pytest.mark.asyncio
async def test_resolve_department_scoped(db_session):
    # A non-full-access user in a department with active cost centers gets
    # scoped to exactly that department's active cost centers (inactive
    # excluded, other departments excluded).
    dept_id = uuid.uuid4()
    other_dept_id = uuid.uuid4()
    uid = uuid.uuid4()
    cc1, cc2, cc_inactive, cc_other = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    )

    await db_session.execute(
        sa.text(
            "INSERT INTO users (id, department_id, role, is_active) "
            "VALUES (CAST(:id AS uuid), CAST(:dept AS uuid), 'requester', true)"
        ),
        {"id": str(uid), "dept": str(dept_id)},
    )
    await db_session.execute(
        sa.text(
            "INSERT INTO cost_centers (id, code, name, department_id, is_active) VALUES "
            "(CAST(:c1 AS uuid), 'CC1', 'CC One', CAST(:dept AS uuid), true), "
            "(CAST(:c2 AS uuid), 'CC2', 'CC Two', CAST(:dept AS uuid), true), "
            "(CAST(:c3 AS uuid), 'CC3', 'CC Inactive', CAST(:dept AS uuid), false), "
            "(CAST(:c4 AS uuid), 'CC4', 'CC Other Dept', CAST(:other AS uuid), true)"
        ),
        {
            "c1": str(cc1), "c2": str(cc2), "c3": str(cc_inactive), "c4": str(cc_other),
            "dept": str(dept_id), "other": str(other_dept_id),
        },
    )
    await db_session.commit()

    scope = await resolve_budget_scope(db_session, uid, "requester")
    assert scope.full_access is False
    assert set(scope.cost_center_ids) == {cc1, cc2}


@pytest.mark.asyncio
async def test_resolve_no_department_fails_closed(db_session):
    # User exists with no department -> fail-closed empty scope, not full access.
    uid = uuid.uuid4()
    scope = await resolve_budget_scope(db_session, uid, "requester")
    assert scope.full_access is False
    assert scope.cost_center_ids == []


async def _seed_dept_cc(db_session, dept_id, cc_id, code):
    await db_session.execute(sa.text(
        "INSERT INTO cost_centers (id, code, name, department_id, is_active) "
        "VALUES (CAST(:cc AS uuid), :code, :code, CAST(:d AS uuid), true)"),
        {"cc": str(cc_id), "code": code, "d": str(dept_id)})


async def _make_director(db_session, uid, own_dept, directed_depts, primary_role,
                         additional_roles=()):
    await db_session.execute(sa.text(
        "INSERT INTO users (id, department_id, role, is_active) "
        "VALUES (CAST(:u AS uuid), CAST(:d AS uuid), :r, true)"),
        {"u": str(uid), "d": str(own_dept) if own_dept else None, "r": primary_role})
    for rc in additional_roles:
        await db_session.execute(sa.text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (CAST(:u AS uuid), :r)"),
            {"u": str(uid), "r": rc})
    for d in directed_depts:
        await db_session.execute(sa.text(
            "INSERT INTO approval_dept_routing (dept_id, director_user_id) "
            "VALUES (CAST(:d AS uuid), CAST(:u AS uuid))"),
            {"d": str(d), "u": str(uid)})


@pytest.mark.asyncio
async def test_director_sees_own_and_directed_departments(db_session):
    """The LIVE PRODUCTION SHAPE: primary role dept_manager + ADDITIONAL role
    director. Which departments they direct must come from
    approval_dept_routing.director_user_id, never from a role string."""
    uid = uuid.uuid4()
    dept_a, dept_b, dept_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cc_a, cc_b, cc_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for d, cc, code in ((dept_a, cc_a, "SELL-A"), (dept_b, cc_b, "SELL-B"),
                        (dept_c, cc_c, "SELL-C")):
        await _seed_dept_cc(db_session, d, cc, code)
    await _make_director(db_session, uid, own_dept=dept_a,
                         directed_depts=[dept_a, dept_b, dept_c],
                         primary_role="dept_manager", additional_roles=["director"])

    scope = await resolve_budget_scope(db_session, uid, "dept_manager")
    assert scope.full_access is False          # director must NOT be company-wide
    assert set(scope.cost_center_ids) == {cc_a, cc_b, cc_c}


@pytest.mark.asyncio
async def test_director_own_dept_not_among_directed_is_still_included(db_session):
    uid = uuid.uuid4()
    own, dir1 = uuid.uuid4(), uuid.uuid4()
    cc_own, cc_dir = uuid.uuid4(), uuid.uuid4()
    await _seed_dept_cc(db_session, own, cc_own, "SELL-OWN")
    await _seed_dept_cc(db_session, dir1, cc_dir, "SELL-DIR")
    await _make_director(db_session, uid, own_dept=own, directed_depts=[dir1],
                         primary_role="dept_manager", additional_roles=["director"])

    scope = await resolve_budget_scope(db_session, uid, "dept_manager")
    assert set(scope.cost_center_ids) == {cc_own, cc_dir}


@pytest.mark.asyncio
async def test_director_without_own_department_still_gets_directed(db_session):
    uid = uuid.uuid4()
    dir1 = uuid.uuid4()
    cc_dir = uuid.uuid4()
    await _seed_dept_cc(db_session, dir1, cc_dir, "SELL-DIR2")
    await _make_director(db_session, uid, own_dept=None, directed_depts=[dir1],
                         primary_role="requester", additional_roles=["director"])

    scope = await resolve_budget_scope(db_session, uid, "requester")
    assert scope.cost_center_ids == [cc_dir]   # NOT fail-closed to empty


@pytest.mark.asyncio
async def test_plain_employee_directing_nothing_unchanged(db_session):
    uid = uuid.uuid4()
    own, other = uuid.uuid4(), uuid.uuid4()
    cc_own, cc_other = uuid.uuid4(), uuid.uuid4()
    await _seed_dept_cc(db_session, own, cc_own, "SELL-MINE")
    await _seed_dept_cc(db_session, other, cc_other, "SELL-OTHER")
    await _make_director(db_session, uid, own_dept=own, directed_depts=[],
                         primary_role="requester")

    scope = await resolve_budget_scope(db_session, uid, "requester")
    assert scope.cost_center_ids == [cc_own]
