import uuid
import pytest
import sqlalchemy as sa
from app.core.budget_scope import (
    PERM_VIEW_ALL, PERM_VIEW_DEPT,
    BudgetScope, resolve_budget_scope, scoped_cc_ids,
)


def test_permission_keys_are_pinned():
    # Guards against silent drift vs finance-api's copy of this module and
    # identity migration 0010_budget_view_scope_perms, which registers them.
    assert PERM_VIEW_ALL == "finance.budget.view_all"
    assert PERM_VIEW_DEPT == "finance.budget.view_dept"


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


# ── helpers ──────────────────────────────────────────────────────────────────
#
# conftest seeds the production default grants (identity migration
# 0010_budget_view_scope_perms) for every built-in role, so a test only touches
# role_permissions when it is specifically about a grant being absent.

async def _revoke_budget_perms(db_session, role_code):
    """What an admin does by unticking both budget boxes for a role."""
    await db_session.execute(sa.text(
        "DELETE FROM role_permissions WHERE role_code = :r "
        "AND permission_key IN (:a, :d)"),
        {"r": role_code, "a": PERM_VIEW_ALL, "d": PERM_VIEW_DEPT})


async def _seed_dept_cc(db_session, dept_id, cc_id, code):
    await db_session.execute(sa.text(
        "INSERT INTO cost_centers (id, code, name, department_id, is_active) "
        "VALUES (CAST(:cc AS uuid), :code, :code, CAST(:d AS uuid), true)"),
        {"cc": str(cc_id), "code": code, "d": str(dept_id)})


async def _make_user(db_session, uid, own_dept, primary_role,
                     directed_depts=(), additional_roles=()):
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
            "VALUES (CAST(:d AS uuid), CAST(:u AS uuid)) "
            "ON CONFLICT (dept_id) DO UPDATE SET director_user_id = EXCLUDED.director_user_id"),
            {"d": str(d), "u": str(uid)})


async def _route_to_opm(db_session, dept_id):
    await db_session.execute(sa.text(
        "INSERT INTO approval_dept_routing (dept_id, gm_or_opm) "
        "VALUES (CAST(:d AS uuid), 'opm') "
        "ON CONFLICT (dept_id) DO UPDATE SET gm_or_opm = 'opm'"), {"d": str(dept_id)})


# ── matrix-driven admission ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_view_all_grant_gives_company_wide(db_session):
    scope = await resolve_budget_scope(db_session, uuid.uuid4(), "finance_manager")
    assert scope.full_access is True


@pytest.mark.asyncio
async def test_view_all_via_additional_role(db_session):
    """The grant is on an ADDITIONAL role (finance_bp), the primary role has
    only the department key — the role union must still reach full access."""
    uid = uuid.uuid4()
    await _make_user(db_session, uid, own_dept=uuid.uuid4(),
                     primary_role="requester", additional_roles=["finance_bp"])
    await db_session.commit()
    scope = await resolve_budget_scope(db_session, uid, "requester")
    assert scope.full_access is True


@pytest.mark.asyncio
async def test_system_admin_short_circuits_without_a_grant(db_session):
    # uniops_authz admits system_admin for every key without consulting the
    # matrix — the Access Control UI cannot lock the admin out of the dashboard.
    scope = await resolve_budget_scope(db_session, uuid.uuid4(), "system_admin")
    assert scope.full_access is True


@pytest.mark.asyncio
async def test_no_budget_permission_fails_closed(db_session):
    """A role holding NEITHER key sees nothing — even with a department full of
    cost centers. This is the whole point of the matrix: revoking both keys
    turns the dashboard off for that role."""
    uid, dept = uuid.uuid4(), uuid.uuid4()
    await _seed_dept_cc(db_session, dept, uuid.uuid4(), "NOPERM-CC")
    await _make_user(db_session, uid, own_dept=dept, primary_role="warehouse_staff")
    await _revoke_budget_perms(db_session, "warehouse_staff")
    await db_session.commit()
    scope = await resolve_budget_scope(db_session, uid, "warehouse_staff")
    assert scope.full_access is False
    assert scope.cost_center_ids == []


@pytest.mark.asyncio
async def test_view_dept_scoped_to_own_department(db_session):
    # Scoped to exactly the user's department's ACTIVE cost centers (inactive
    # excluded, other departments excluded).
    dept_id, other_dept_id, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cc1, cc2, cc_inactive, cc_other = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    )
    await _make_user(db_session, uid, own_dept=dept_id, primary_role="requester")
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
async def test_view_dept_without_a_department_fails_closed(db_session):
    # Holds the department key but has no department at all -> empty, never
    # a fallback to company-wide.
    scope = await resolve_budget_scope(db_session, uuid.uuid4(), "requester")
    assert scope.full_access is False
    assert scope.cost_center_ids == []


# ── director departments ─────────────────────────────────────────────────────

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
    await _make_user(db_session, uid, own_dept=dept_a, primary_role="dept_manager",
                     directed_depts=[dept_a, dept_b, dept_c],
                     additional_roles=["director"])
    await db_session.commit()

    scope = await resolve_budget_scope(db_session, uid, "dept_manager")
    assert scope.full_access is False          # director must NOT be company-wide
    assert set(scope.cost_center_ids) == {cc_a, cc_b, cc_c}


@pytest.mark.asyncio
async def test_director_without_own_department_still_gets_directed(db_session):
    uid, dir1, cc_dir = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_dept_cc(db_session, dir1, cc_dir, "SELL-DIR2")
    await _make_user(db_session, uid, own_dept=None, primary_role="requester",
                     directed_depts=[dir1], additional_roles=["director"])
    await db_session.commit()

    scope = await resolve_budget_scope(db_session, uid, "requester")
    assert scope.cost_center_ids == [cc_dir]   # NOT fail-closed to empty


# ── OPM departments ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_opm_sees_every_department_routed_to_the_opm(db_session):
    """The OPM is a POST, not a per-user department assignment: the only link
    from a department to "the OPM is responsible for it" is
    approval_dept_routing.gm_or_opm = 'opm'. Departments routed to the GM
    (the column default) must stay out."""
    uid, own, opm_a, opm_b, gm_dept = (uuid.uuid4() for _ in range(5))
    cc_own, cc_a, cc_b, cc_gm = (uuid.uuid4() for _ in range(4))
    for d, cc, code in ((own, cc_own, "OPM-OWN"), (opm_a, cc_a, "OPM-A"),
                        (opm_b, cc_b, "OPM-B"), (gm_dept, cc_gm, "GM-ONLY")):
        await _seed_dept_cc(db_session, d, cc, code)
    await _make_user(db_session, uid, own_dept=own, primary_role="opm")
    for d in (opm_a, opm_b):
        await _route_to_opm(db_session, d)
    await db_session.execute(sa.text(
        "INSERT INTO approval_dept_routing (dept_id, gm_or_opm) "
        "VALUES (CAST(:d AS uuid), 'gm')"), {"d": str(gm_dept)})
    await db_session.commit()

    scope = await resolve_budget_scope(db_session, uid, "opm")
    assert scope.full_access is False          # opm is no longer company-wide
    assert set(scope.cost_center_ids) == {cc_own, cc_a, cc_b}


@pytest.mark.asyncio
async def test_opm_as_an_additional_role_also_counts(db_session):
    uid, own, opm_dept = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cc_own, cc_opm = uuid.uuid4(), uuid.uuid4()
    await _seed_dept_cc(db_session, own, cc_own, "ADD-OWN")
    await _seed_dept_cc(db_session, opm_dept, cc_opm, "ADD-OPM")
    await _make_user(db_session, uid, own_dept=own, primary_role="dept_manager",
                     additional_roles=["opm"])
    await _route_to_opm(db_session, opm_dept)
    await db_session.commit()

    scope = await resolve_budget_scope(db_session, uid, "dept_manager")
    assert set(scope.cost_center_ids) == {cc_own, cc_opm}


@pytest.mark.asyncio
async def test_non_opm_does_not_inherit_opm_routed_departments(db_session):
    uid, own, opm_dept = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cc_own, cc_opm = uuid.uuid4(), uuid.uuid4()
    await _seed_dept_cc(db_session, own, cc_own, "PLAIN-OWN")
    await _seed_dept_cc(db_session, opm_dept, cc_opm, "PLAIN-OPM")
    await _make_user(db_session, uid, own_dept=own, primary_role="dept_manager")
    await _route_to_opm(db_session, opm_dept)
    await db_session.commit()

    scope = await resolve_budget_scope(db_session, uid, "dept_manager")
    assert scope.cost_center_ids == [cc_own]


# ── fail-closed ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolution_error_fails_closed(db_session):
    """If any lookup in the resolution chain blows up — e.g. an environment
    where approval-api's migrations haven't created approval_dept_routing —
    the whole thing must fail CLOSED to an empty scope, never raise (500)
    and never grant full_access."""
    uid = uuid.uuid4()
    await _make_user(db_session, uid, own_dept=uuid.uuid4(), primary_role="requester")
    await db_session.commit()
    await db_session.execute(sa.text("DROP TABLE approval_dept_routing"))
    await db_session.commit()

    scope = await resolve_budget_scope(db_session, uid, "requester")
    assert scope.full_access is False
    assert scope.cost_center_ids == []


@pytest.mark.asyncio
async def test_missing_matrix_fails_closed_not_open(db_session):
    """No role_permissions table at all (matrix not migrated yet) must read as
    "no permission", never as full access."""
    uid = uuid.uuid4()
    await _make_user(db_session, uid, own_dept=uuid.uuid4(), primary_role="finance_manager")
    await db_session.commit()
    await db_session.execute(sa.text("DROP TABLE role_permissions"))
    await db_session.commit()

    scope = await resolve_budget_scope(db_session, uid, "finance_manager")
    assert scope.full_access is False
    assert scope.cost_center_ids == []
