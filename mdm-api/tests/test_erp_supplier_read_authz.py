"""Reading the ERP supplier mirror follows the Access Control matrix.

The EPMS "From ERP" vendor-import drawer lists these suppliers, and it shows
its button on the `vendor_master` matrix key. This read used to be gated on
the caller's PRIMARY role being system_admin|vendor_manager, so every other
role the matrix had granted vendor_master to (ap_clerk, procurement_officer,
…) got a 403 that the drawer rendered as an empty supplier list.

Like tests/test_authz_any_permission.py, these tests monkeypatch the
`has_permission` primitive the gate is built on — mdm-api's alembic chain does
not own identity's role/permission tables, so the matrix cannot be seeded here.
"""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

SUPPLIERS = "/mdm/v1/erp/suppliers"


@pytest_asyncio.fixture
async def client_as(db_session):
    """Like the shared `client` fixture but lets each test pick the role —
    the shared one is pinned to system_admin, which short-circuits every gate."""
    from app.main import app
    from app.db.base import get_db
    from app.core.deps import get_token_payload

    async def _override_db():
        yield db_session

    def _make(role: str) -> AsyncClient:
        async def _override_user():
            return {"sub": str(uuid.uuid4()), "role": role, "type": "access"}

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_token_payload] = _override_user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _make
    app.dependency_overrides.clear()


def _grant(monkeypatch, *granted: str):
    import app.core.authz as authz_module

    async def _has(db, uid, role, key):
        return key in granted

    monkeypatch.setattr(authz_module, "has_permission", _has)


@pytest.mark.anyio
async def test_vendor_master_holder_can_list_suppliers(client_as, monkeypatch):
    """The regression: ap_clerk holds vendor_master in the matrix, so it sees
    the "From ERP" button — and must therefore get the list, not a 403."""
    _grant(monkeypatch, "vendor_master")
    async with client_as("ap_clerk") as c:
        resp = await c.get(SUPPLIERS)
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.anyio
async def test_mdm_vendor_write_alone_also_grants_the_read(client_as, monkeypatch):
    """finance_manager is seeded with mdm.vendor.write but not vendor_master."""
    _grant(monkeypatch, "mdm.vendor.write")
    async with client_as("finance_manager") as c:
        resp = await c.get(SUPPLIERS)
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_role_holding_neither_key_is_still_403(client_as, monkeypatch):
    _grant(monkeypatch)  # nothing granted
    async with client_as("requester") as c:
        resp = await c.get(SUPPLIERS)
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_system_admin_bypasses_the_matrix(client_as, monkeypatch):
    import app.core.authz as authz_module

    async def _boom(*a, **kw):
        raise AssertionError("has_permission must not be called for system_admin")

    monkeypatch.setattr(authz_module, "has_permission", _boom)
    async with client_as("system_admin") as c:
        resp = await c.get(SUPPLIERS)
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_supplier_by_code_uses_the_same_gate(client_as, monkeypatch):
    """404 (not 403) for a granted role proves the gate let the request through."""
    _grant(monkeypatch, "vendor_master")
    async with client_as("procurement_officer") as c:
        granted = await c.get(f"{SUPPLIERS}/NO-SUCH-CODE")
    assert granted.status_code == 404

    _grant(monkeypatch)
    async with client_as("procurement_officer") as c:
        denied = await c.get(f"{SUPPLIERS}/NO-SUCH-CODE")
    assert denied.status_code == 403
