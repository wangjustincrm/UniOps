"""Dashboard identity resolution — additional roles, and the scoped-approver roles.

`GET /dashboard` used to branch on the JWT's PRIMARY role only. Two consequences:

1. `build_payment_officer` was dead code. payment_officer is an ADDITIONAL role
   (never a primary one — see identity migration 0009), so its branch could
   never be reached and the holder always got their primary role's dashboard.
2. director / supervisor / dept_admin fell through to the requester payload,
   while the frontend has always routed them to the Approver dashboard — so
   their "Pending Approvals" list was structurally empty (the requester payload
   carries no pending_approvals at all).

Resolution order is now: primary role if it owns a dashboard, else the first
additional role that owns one, else requester. The response's `role` field is
what the frontend switches on, so backend and frontend can no longer disagree.
"""
import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio
DASH_URL = "/api/v1/dashboard"




async def _client_with_roles(test_engine, primary: str, additional: list[str] = []):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"{primary}-{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass1!", full_name=f"Test {primary}", role=primary))
        await db.commit()
        for code in additional:
            await db.execute(sa.text(
                "INSERT INTO user_roles(user_id, role_code) VALUES (:u,:r) "
                "ON CONFLICT DO NOTHING"), {"u": str(user.id), "r": code})
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _role_of(client) -> str:
    r = await client.get(DASH_URL)
    assert r.status_code == 200, r.text
    return r.json()["role"]


async def test_payment_officer_additional_role_gets_its_dashboard(test_engine):
    async with await _client_with_roles(test_engine, "requester", ["payment_officer"]) as c:
        r = await c.get(DASH_URL)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "payment_officer"
    assert [k["title"] for k in body["kpis"]][0] == "PAs Awaiting Payment"


async def test_primary_role_with_own_dashboard_wins_over_additional(test_engine):
    """An AP Clerk who also holds payment_officer keeps the AP dashboard — the
    primary role is the job, the additional role is an extra hat."""
    async with await _client_with_roles(test_engine, "ap_clerk", ["payment_officer"]) as c:
        assert await _role_of(c) == "ap_clerk"


async def test_plain_requester_unchanged(test_engine):
    async with await _client_with_roles(test_engine, "requester") as c:
        assert await _role_of(c) == "requester"


async def test_additional_role_without_a_dashboard_falls_back_to_requester(test_engine):
    async with await _client_with_roles(test_engine, "requester", ["erp_pa_officer"]) as c:
        assert await _role_of(c) == "requester"


@pytest.mark.parametrize("role", ["director", "supervisor", "dept_admin"])
async def test_scoped_approver_roles_get_the_approver_dashboard(test_engine, role):
    async with await _client_with_roles(test_engine, role) as c:
        r = await c.get(DASH_URL)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "approver"
    # The requester payload has no pending_approvals key at all — that was the bug.
    assert body["pending_approvals"] is not None


# `role` in the response is the DASHBOARD KIND, not a role code — 'warehouse' for
# warehouse_staff, 'procurement' for either procurement role, 'approver' for the
# approving posts. The frontend router switches on these values.
@pytest.mark.parametrize("role,kind", [
    ("dept_manager", "approver"), ("gm", "approver"), ("opm", "approver"),
    ("procurement_officer", "procurement"), ("procurement_manager", "procurement"),
    ("warehouse_staff", "warehouse"), ("ap_clerk", "ap_clerk"),
    ("finance_bp", "finance_bp"), ("finance_manager", "finance_manager"),
    ("cfo", "cfo"), ("auditor", "auditor"), ("vendor_manager", "vendor_manager"),
    ("system_admin", "system_admin"),
])
async def test_existing_primary_role_dashboards_unchanged(test_engine, role, kind):
    async with await _client_with_roles(test_engine, role) as c:
        assert await _role_of(c) == kind
