"""Department scope on the unified invoice list — the branch that never ran.

`_build_invoice_scope`'s dept_manager/dept_admin branch read the caller's
department from `user.get("department_id")`, i.e. off the JWT. No token in this
platform has ever carried that claim: `create_access_token` (epms-api and
identity-api alike) emits `{sub, role, type}` and nothing else. So the branch
was dead in production — `dept_id_raw` was always None, no PO subquery was
appended, and a department manager or department admin fell through to
`epms_own_uploads` only, seeing just the invoices they had personally uploaded.

Same root cause as the blank Employee column
(test_expense_employee_name.py): reading from the token what only the database
has. epms-api resolves it from `users.department_id`
(app/core/access_scope.py) and this now mirrors that.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.main import create_app
from tests.test_invoice_list_gm_opm_scope import _ids, _make_epms_invoice_chain


def _client_for(role: str, user_id: str) -> AsyncClient:
    token = jwt.encode(
        {"sub": user_id, "role": role, "exp": datetime.now(timezone.utc) + timedelta(hours=8)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
async def identity_db(test_engine):
    """Clean users / user_roles (both shadowed session-wide by conftest)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text("DELETE FROM users"))
        await s.execute(text("DELETE FROM user_roles"))
        await s.commit()
        yield s
        await s.execute(text("DELETE FROM users"))
        await s.execute(text("DELETE FROM user_roles"))
        await s.commit()


async def _seed_user(db, user_id: uuid.UUID, dept_id: uuid.UUID | None,
                     additional: str | None = None) -> None:
    await db.execute(text(
        "INSERT INTO users (id, full_name, department_id) VALUES (:u, 'Test User', :d)"),
        {"u": str(user_id), "d": str(dept_id) if dept_id else None})
    if additional:
        await db.execute(text(
            "INSERT INTO role_defs (code, is_active) VALUES (:c, true) "
            "ON CONFLICT (code) DO NOTHING"), {"c": additional})
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :c)"),
            {"u": str(user_id), "c": additional})
    await db.commit()


@pytest.mark.asyncio
async def test_dept_manager_sees_own_department_invoice(identity_db):
    """The reported shape: a department manager sees their department's chain."""
    dept_id, uid = uuid.uuid4(), uuid.uuid4()
    await _seed_user(identity_db, uid, dept_id)
    inv_id = await _make_epms_invoice_chain(identity_db, dept_id)

    async with _client_for("dept_manager", str(uid)) as c:
        resp = await c.get("/api/v1/invoices/all")
    assert resp.status_code == 200
    assert str(inv_id) in _ids(resp.json())


@pytest.mark.asyncio
async def test_dept_admin_as_additional_role_sees_department_invoice(identity_db):
    """Kris's shape: JWT role requester, dept_admin held in user_roles."""
    dept_id, uid = uuid.uuid4(), uuid.uuid4()
    await _seed_user(identity_db, uid, dept_id, additional="dept_admin")
    inv_id = await _make_epms_invoice_chain(identity_db, dept_id)

    async with _client_for("requester", str(uid)) as c:
        resp = await c.get("/api/v1/invoices/all")
    assert resp.status_code == 200
    assert str(inv_id) in _ids(resp.json())


@pytest.mark.asyncio
async def test_dept_manager_does_not_see_other_department_invoice(identity_db):
    """The scope must stay a scope — not degrade into "return everything"."""
    own_dept, other_dept, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_user(identity_db, uid, own_dept)
    inv_id = await _make_epms_invoice_chain(identity_db, other_dept)

    async with _client_for("dept_manager", str(uid)) as c:
        resp = await c.get("/api/v1/invoices/all")
    assert resp.status_code == 200
    assert str(inv_id) not in _ids(resp.json())


@pytest.mark.asyncio
async def test_dept_manager_without_department_row_sees_nothing_extra(identity_db):
    """Fail-closed: no users row (or a NULL department) grants no dept scope."""
    dept_id, uid = uuid.uuid4(), uuid.uuid4()
    await _seed_user(identity_db, uid, None)
    inv_id = await _make_epms_invoice_chain(identity_db, dept_id)

    async with _client_for("dept_manager", str(uid)) as c:
        resp = await c.get("/api/v1/invoices/all")
    assert resp.status_code == 200
    assert str(inv_id) not in _ids(resp.json())
