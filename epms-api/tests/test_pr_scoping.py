"""PRD §3.2.3 — PR list visibility scoping (FR IDs: PL-001, PL-004, PL-005).

Creates users with specific roles, verifies that GET /api/v1/pr returns only
the PRs each role should see.
"""
import uuid
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.schemas.auth import RegisterRequest


async def _make_user(test_engine, role: str, department_id: str | None = None) -> tuple[str, str]:
    """Create user in test DB, return (user_id, token).

    Optionally pins the user to a department (User.department_id) — needed for
    dept_manager / requester scoping tests.
    """
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(
            db,
            RegisterRequest(
                email=f"{role}-{uuid.uuid4().hex[:6]}@scope-test.com",
                password="TestPass1!",
                full_name=f"Scope {role}",
                role=role,
            ),
        )
        if department_id is not None:
            user.department_id = uuid.UUID(department_id)
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    return str(user.id), token


async def _make_dept(test_engine, code: str | None = None) -> str:
    """Insert a department directly (master data — no POST endpoint). Returns id."""
    from app.models.department import Department

    code = code or f"D{uuid.uuid4().hex[:4].upper()}"
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        dept = Department(code=code, name=f"Dept {code}", is_active=True)
        db.add(dept)
        await db.commit()
        await db.refresh(dept)
        return str(dept.id)


async def _make_cc(test_engine, dept_id: str, code: str | None = None) -> str:
    """Insert a cost center directly. Returns id."""
    from app.models.cost_center import CostCenter

    code = code or f"CC{uuid.uuid4().hex[:4].upper()}"
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        cc = CostCenter(
            code=code, name=f"CostCenter {code}", is_active=True,
            department_id=uuid.UUID(dept_id),
        )
        db.add(cc)
        await db.commit()
        await db.refresh(cc)
        return str(cc.id)


def _authed_client(token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


_PR_BASE = {
    "title": "Scoping Test PR",
    "type": 3,
    "currency": "CAD",
    "line_items": [
        {"description": "Part A", "material_id": "M-001",
         "qty": "1", "unit": "EA", "unit_price": "10.00"}
    ],
}


@pytest.mark.asyncio
async def test_pl001_requester_cannot_see_other_requester_pr(test_engine):
    """PL-001: server scope is unconditional — Requester B cannot see Requester A's PRs."""
    uid_a, tok_a = await _make_user(test_engine, "requester")
    uid_b, tok_b = await _make_user(test_engine, "requester")

    async with _authed_client(tok_a) as ca:
        r = await ca.post("/api/v1/pr", json=_PR_BASE)
        assert r.status_code == 201

    async with _authed_client(tok_b) as cb:
        resp = await cb.get("/api/v1/pr")
        assert resp.status_code == 200
        creators = [p["created_by"] for p in resp.json()["items"]]
        assert uid_a not in creators, "PL-001: Requester B saw Requester A's PR"


@pytest.mark.asyncio
async def test_pl001_requester_sees_own_prs(test_engine):
    """PL-001: Requester sees their own PRs."""
    uid, tok = await _make_user(test_engine, "requester")
    async with _authed_client(tok) as c:
        r = await c.post("/api/v1/pr", json=_PR_BASE)
        assert r.status_code == 201
        pr_id = r.json()["id"]

        resp = await c.get("/api/v1/pr")
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()["items"]]
        assert pr_id in ids, "PL-001: Requester does not see their own PR"


@pytest.mark.asyncio
async def test_pl004_finance_manager_sees_all_prs(test_engine, admin_client):
    """PL-004: Finance Manager sees all PRs regardless of department."""
    r = await admin_client.post("/api/v1/pr", json=_PR_BASE)
    assert r.status_code == 201

    _, tok_fm = await _make_user(test_engine, "finance_manager")
    async with _authed_client(tok_fm) as c:
        resp = await c.get("/api/v1/pr")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1, "PL-004: Finance Manager should see all PRs"


@pytest.mark.asyncio
async def test_pl004_procurement_officer_sees_all_prs(test_engine, admin_client):
    """PL-004: Procurement Officer sees all PRs."""
    r = await admin_client.post("/api/v1/pr", json=_PR_BASE)
    assert r.status_code == 201

    _, tok_po = await _make_user(test_engine, "procurement_officer")
    async with _authed_client(tok_po) as c:
        resp = await c.get("/api/v1/pr")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1, "PL-004: Procurement Officer should see all PRs"


@pytest.mark.asyncio
async def test_dept_manager_can_open_pr_from_own_dept_member_cross_cost_center(
    test_engine,
):
    """Regression (PR-20260620-0001): a dept_manager receives the approve_pr task
    routed by the *requester's* department (approval-api _get_dept_manager_id),
    so they must be able to OPEN the PR detail even when the PR is charged to a
    cost center belonging to a DIFFERENT department.

    Before the fix, PR visibility scoped only by the PR's cost-center department,
    so the manager got the inbox task but 404'd on GET /pr/{id}.
    """
    # Seed the singleton CompanyConfig so the Access Control Matrix grants the
    # default dept_manager view_pr permission (otherwise perms default to all-False
    # and the 404 would be a permission artifact, not the scoping bug under test).
    from app.crud import config as config_crud
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await config_crud.get_or_create(db)
        await db.commit()

    dept_a = await _make_dept(test_engine)
    dept_b = await _make_dept(test_engine)
    cc_b = await _make_cc(test_engine, dept_b)  # cost center in the OTHER dept

    # Requester + their dept_manager both belong to dept A.
    _, tok_req = await _make_user(test_engine, "requester", department_id=dept_a)
    _, tok_mgr = await _make_user(test_engine, "dept_manager", department_id=dept_a)

    # Requester raises a PR charged to dept B's cost center.
    async with _authed_client(tok_req) as c:
        r = await c.post("/api/v1/pr", json={**_PR_BASE, "cost_center_id": cc_b})
        assert r.status_code == 201, r.text
        pr_id = r.json()["id"]

    # The dept_manager of the requester's department must be able to open it.
    async with _authed_client(tok_mgr) as c:
        resp = await c.get(f"/api/v1/pr/{pr_id}")
        assert resp.status_code == 200, (
            f"dept_manager 404'd on a PR raised by their own department member "
            f"(status={resp.status_code})"
        )


@pytest.mark.asyncio
async def test_pl005_scope_cannot_be_bypassed_via_query_params(test_engine):
    """PL-005: server scope ignores client params — mine=false cannot expose other users' PRs."""
    uid_a, tok_a = await _make_user(test_engine, "requester")
    uid_b, tok_b = await _make_user(test_engine, "requester")

    async with _authed_client(tok_a) as ca:
        await ca.post("/api/v1/pr", json=_PR_BASE)

    async with _authed_client(tok_b) as cb:
        resp = await cb.get("/api/v1/pr?mine=false")
        assert resp.status_code == 200
        creators = [p["created_by"] for p in resp.json()["items"]]
        assert uid_a not in creators, "PL-005: mine=false bypassed server scope"


@pytest.mark.asyncio
async def test_director_sees_mapped_dept_prs(test_engine):
    """Director sees PRs from departments mapped to them, not from unmapped departments."""
    from app.crud import config as config_crud
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Seed CompanyConfig with view_pr enabled for director
    async with factory() as db:
        cfg = await config_crud.get_or_create(db)
        await db.commit()

    # Create two departments: dept_d (mapped to director) and dept_other (not mapped)
    dept_d = await _make_dept(test_engine)
    dept_other = await _make_dept(test_engine)

    # Create a director user (base role "director")
    uid_dir, tok_dir = await _make_user(test_engine, "director")

    # Map dept_d to director via approval_dept_routing.director_user_id — phase-3
    # Task 2 moved _director_dept_ids off CompanyConfig.dept_director_mapping onto
    # approval-api's approval_dept_routing table (same physical DB, read-only from
    # epms). approval_dept_routing is owned by approval-api's own alembic head, so
    # epms_test won't have it by default — CREATE TABLE IF NOT EXISTS here (schema
    # copied from approval-api/alembic/versions/0001_approval_routing.py) so this
    # test is self-sufficient even when run in isolation from
    # test_access_scope_dept.py (whose fixture also creates this table).
    async with factory() as db:
        await db.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " gm_or_opm varchar(3) NOT NULL DEFAULT 'gm',"
            " director_user_id uuid NULL,"
            " supervisor_enabled boolean NOT NULL DEFAULT false,"
            " updated_by uuid NULL,"
            " updated_at timestamptz NOT NULL DEFAULT now()"
            ")"
        ))
        await db.execute(sa.text("DELETE FROM approval_dept_routing WHERE dept_id = :d"), {"d": dept_d})
        await db.execute(sa.text(
            "INSERT INTO approval_dept_routing (dept_id, gm_or_opm, director_user_id, supervisor_enabled) "
            "VALUES (:d, 'gm', :dir, false)"),
            {"d": dept_d, "dir": uid_dir})
        await db.commit()

    # Create a requester in dept_d and one in dept_other
    uid_req_d, tok_req_d = await _make_user(test_engine, "requester", department_id=dept_d)
    uid_req_other, tok_req_other = await _make_user(test_engine, "requester", department_id=dept_other)

    # Requester in mapped dept creates a PR
    async with _authed_client(tok_req_d) as c:
        r = await c.post("/api/v1/pr", json=_PR_BASE)
        assert r.status_code == 201, r.text
        pr_id_in_dept = r.json()["id"]

    # Requester in unmapped dept creates a PR
    async with _authed_client(tok_req_other) as c:
        r = await c.post("/api/v1/pr", json=_PR_BASE)
        assert r.status_code == 201, r.text
        pr_id_other_dept = r.json()["id"]

    # Director can open the PR from their mapped department
    async with _authed_client(tok_dir) as c:
        resp = await c.get(f"/api/v1/pr/{pr_id_in_dept}")
        assert resp.status_code == 200, (
            f"Director could not open PR from mapped dept (status={resp.status_code})"
        )

    # Director cannot open the PR from the unmapped department
    async with _authed_client(tok_dir) as c:
        resp = await c.get(f"/api/v1/pr/{pr_id_other_dept}")
        assert resp.status_code == 404, (
            f"Director saw PR from unmapped dept (status={resp.status_code})"
        )


@pytest.mark.asyncio
async def test_additional_role_widens_scope(test_engine, admin_client):
    """Phase 3 Task 5: `_effective_role_codes` now unions the JWT base role with
    ADDITIONAL roles read straight from identity's `user_roles` table (same
    physical DB), replacing the retired `company_config.role_management`
    assignments. A 'requester' (own-PRs-only scope) holding an ADDITIONAL
    finance_manager role must get the same unrestricted view a primary
    finance_manager gets (PL-004) — proving the union is read from user_roles,
    not the old JSONB."""
    r = await admin_client.post("/api/v1/pr", json=_PR_BASE)
    assert r.status_code == 201, r.text

    uid, tok = await _make_user(test_engine, "requester")
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(sa.text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_manager')"),
            {"u": uid})
        await db.commit()

    async with _authed_client(tok) as c:
        resp = await c.get("/api/v1/pr")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1, (
            "requester holding an ADDITIONAL finance_manager role (user_roles) "
            "should see all PRs, same as PL-004 for a primary finance_manager"
        )


@pytest.mark.asyncio
async def test_multi_restricted_role_unions_scope_dept_manager_plus_gm(test_engine):
    """Multi-role bug (hanchenggang: Department Manager + GM): when a user holds
    TWO restricted roles, PR visibility must be the UNION of both roles' scopes,
    not just the single JWT base role's scope.

    Before the fix, `visible_pr_subquery` branched on the single base JWT role
    (`dept_manager`) and never reached the `gm` branch, so a dept_manager who is
    ALSO a GM saw only their own department's PRs — the PRs of the departments
    their GM role covers were invisible in the PA/PR/PO lists. `_has_unrestricted_
    special_role` did NOT rescue this because gm/dept_manager are BOTH restricted
    roles (neither grants unrestricted scope), so the multi-role union path that
    already works for unrestricted additional roles (test_additional_role_widens_
    scope) never fired for two restricted roles.
    """
    from app.crud import config as config_crud

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await config_crud.get_or_create(db)   # default matrix grants dept_manager/gm view_pr
        await db.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " gm_or_opm varchar(3) NOT NULL DEFAULT 'gm',"
            " director_user_id uuid NULL,"
            " supervisor_enabled boolean NOT NULL DEFAULT false,"
            " updated_by uuid NULL,"
            " updated_at timestamptz NOT NULL DEFAULT now()"
            ")"
        ))
        await db.commit()

    dept_mgr_own = await _make_dept(test_engine)   # the user's OWN department (dept_manager scope)
    dept_gm = await _make_dept(test_engine)        # a department the user covers as GM
    cc_gm = await _make_cc(test_engine, dept_gm)   # PR will be charged to this GM-dept cost center

    # Map dept_gm to 'gm' so _mapped_dept_ids('gm') resolves it to our user's GM scope.
    async with factory() as db:
        await db.execute(sa.text("DELETE FROM approval_dept_routing WHERE dept_id = :d"), {"d": dept_gm})
        await db.execute(sa.text(
            "INSERT INTO approval_dept_routing (dept_id, gm_or_opm, supervisor_enabled) "
            "VALUES (:d, 'gm', false)"), {"d": dept_gm})
        await db.commit()

    # The multi-role user: base JWT role dept_manager (pinned to their own dept) +
    # an ADDITIONAL gm role in user_roles.
    uid_mgr, tok_mgr = await _make_user(test_engine, "dept_manager", department_id=dept_mgr_own)
    async with factory() as db:
        await db.execute(sa.text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'gm')"), {"u": uid_mgr})
        await db.commit()

    # A requester in the GM-covered department raises a PR charged to that dept's
    # cost center — outside the manager's own department entirely.
    _, tok_req_gm = await _make_user(test_engine, "requester", department_id=dept_gm)
    async with _authed_client(tok_req_gm) as c:
        r = await c.post("/api/v1/pr", json={**_PR_BASE, "cost_center_id": cc_gm})
        assert r.status_code == 201, r.text
        pr_in_gm_dept = r.json()["id"]

    # The dept_manager+gm user MUST see the GM-department PR in their list.
    async with _authed_client(tok_mgr) as c:
        resp = await c.get("/api/v1/pr")
        assert resp.status_code == 200, resp.text
        ids = [p["id"] for p in resp.json()["items"]]
        assert pr_in_gm_dept in ids, (
            "dept_manager+gm user did not see a PR from a department their GM role "
            "covers — multi-restricted-role scope was NOT unioned"
        )


@pytest.mark.asyncio
async def test_multi_role_dept_manager_plus_director_unions_scope(test_engine):
    """Director spans multiple departments too. A user who is a dept_manager AND
    a director (approval_dept_routing.director_user_id) must see BOTH their own
    department's PRs and the PRs of the departments they direct — the union must
    include the director-derived scope, not only the base dept_manager scope.
    """
    from app.crud import config as config_crud

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await config_crud.get_or_create(db)
        await db.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " gm_or_opm varchar(3) NOT NULL DEFAULT 'gm',"
            " director_user_id uuid NULL,"
            " supervisor_enabled boolean NOT NULL DEFAULT false,"
            " updated_by uuid NULL,"
            " updated_at timestamptz NOT NULL DEFAULT now()"
            ")"
        ))
        await db.commit()

    own_dept = await _make_dept(test_engine)       # user's dept_manager department
    directed_dept = await _make_dept(test_engine)  # a department they DIRECT
    cc_dir = await _make_cc(test_engine, directed_dept)

    uid_mgr, tok_mgr = await _make_user(test_engine, "dept_manager", department_id=own_dept)
    # Make the user the director of directed_dept (derives the 'director' role).
    async with factory() as db:
        await db.execute(sa.text("DELETE FROM approval_dept_routing WHERE dept_id = :d"), {"d": directed_dept})
        await db.execute(sa.text(
            "INSERT INTO approval_dept_routing (dept_id, gm_or_opm, director_user_id, supervisor_enabled) "
            "VALUES (:d, 'gm', :dir, false)"), {"d": directed_dept, "dir": uid_mgr})
        await db.commit()

    _, tok_req = await _make_user(test_engine, "requester", department_id=directed_dept)
    async with _authed_client(tok_req) as c:
        r = await c.post("/api/v1/pr", json={**_PR_BASE, "cost_center_id": cc_dir})
        assert r.status_code == 201, r.text
        pr_in_directed = r.json()["id"]

    async with _authed_client(tok_mgr) as c:
        resp = await c.get("/api/v1/pr")
        assert resp.status_code == 200, resp.text
        ids = [p["id"] for p in resp.json()["items"]]
        assert pr_in_directed in ids, (
            "dept_manager+director user did not see a PR from a department they "
            "direct — director scope was NOT unioned"
        )


@pytest.mark.asyncio
async def test_supervisor_sees_only_direct_reports_prs(test_engine):
    """Supervisor sees PRs created by their direct reports, not by unrelated users."""
    from app.crud import config as config_crud
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Seed CompanyConfig with view_pr enabled for supervisor
    async with factory() as db:
        await config_crud.get_or_create(db)
        await db.commit()

    # Create supervisor user (base role "supervisor")
    uid_sup, tok_sup = await _make_user(test_engine, "supervisor")

    # Create a direct report: a requester whose supervisor_id = uid_sup
    uid_report, tok_report = await _make_user(test_engine, "requester")
    async with factory() as db:
        from app.models.user import User as UserModel
        from sqlalchemy import select as sa_select
        user_obj = (await db.execute(sa_select(UserModel).where(UserModel.id == uuid.UUID(uid_report)))).scalar_one()
        user_obj.supervisor_id = uuid.UUID(uid_sup)
        await db.commit()

    # Create an unrelated requester (no supervisor link to uid_sup)
    uid_unrelated, tok_unrelated = await _make_user(test_engine, "requester")

    # Direct report creates a PR
    async with _authed_client(tok_report) as c:
        r = await c.post("/api/v1/pr", json=_PR_BASE)
        assert r.status_code == 201, r.text
        pr_id_report = r.json()["id"]

    # Unrelated requester creates a PR
    async with _authed_client(tok_unrelated) as c:
        r = await c.post("/api/v1/pr", json=_PR_BASE)
        assert r.status_code == 201, r.text
        pr_id_unrelated = r.json()["id"]

    # Supervisor can open the direct report's PR
    async with _authed_client(tok_sup) as c:
        resp = await c.get(f"/api/v1/pr/{pr_id_report}")
        assert resp.status_code == 200, (
            f"Supervisor could not open direct report's PR (status={resp.status_code})"
        )

    # Supervisor cannot open an unrelated user's PR
    async with _authed_client(tok_sup) as c:
        resp = await c.get(f"/api/v1/pr/{pr_id_unrelated}")
        assert resp.status_code == 404, (
            f"Supervisor saw unrelated user's PR (status={resp.status_code})"
        )
