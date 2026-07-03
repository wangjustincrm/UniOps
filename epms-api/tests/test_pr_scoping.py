"""PRD §3.2.3 — PR list visibility scoping (FR IDs: PL-001, PL-004, PL-005).

Creates users with specific roles, verifies that GET /api/v1/pr returns only
the PRs each role should see.
"""
import uuid
import pytest
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

    # Map dept_d to director in CompanyConfig.dept_director_mapping
    async with factory() as db:
        cfg = await config_crud.get_or_create(db)
        cfg.dept_director_mapping = {dept_d: uid_dir}
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(cfg, "dept_director_mapping")
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
