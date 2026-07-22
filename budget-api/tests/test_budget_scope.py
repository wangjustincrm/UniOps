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
