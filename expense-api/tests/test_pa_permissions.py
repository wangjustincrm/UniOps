"""GET /pa/{pa_id}/permissions — server-resolved action rights for the OA PA detail page.

Mirrors the expense-claim /permissions contract: approval roles (Finance BP, Finance
Manager) are role_management assignments, not JWT role claims, so can_approve is
resolved against the shared tasks table + company_config.role_management.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

import app.db.base as db_module
from app.core.config import settings
from app.main import create_app
from app.models.company_config_mirror import EpmsCompanyConfig
from app.models.pa import PaymentApplication
from app.models.task_mirror import TaskMirror


# ── Helpers ────────────────────────────────────────────────────────────────────

def _client_for(role: str, user_id: str) -> AsyncClient:
    token = jwt.encode(
        {"sub": user_id, "role": role, "exp": datetime.utcnow() + timedelta(hours=8)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _make_pa(owner_client) -> dict:
    """Create a reviewed invoice + a draft PA-DIR owned by owner_client's user."""
    inv = (await owner_client.post(
        "/api/v1/invoices",
        json={
            "file_name": "vendor_invoice.pdf",
            "file_mime_type": "application/pdf",
            "file_size_bytes": 204800,
            "invoice_number": f"INV-{uuid.uuid4().hex[:8]}",
            "vendor_id": str(uuid.uuid4()),
            "vendor_name": "Titan Power Ltd",
            "currency": "CAD",
            "subtotal": "1000.00",
            "tax_amount": "130.00",
            "total_amount": "1130.00",
        },
    )).json()
    resp = await owner_client.post(
        "/api/v1/pa/direct",
        json={
            "invoice_id": inv["id"],
            "vendor_name": "Titan Power Ltd",
            "payment_amount": "1130.00",
            "currency": "CAD",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _set_pa_status(pa_id: str, status: str) -> None:
    async with db_module.AsyncSessionLocal() as db:
        pa = await db.get(PaymentApplication, uuid.UUID(pa_id))
        pa.status = status
        await db.commit()


async def _add_open_task(pa_id: str, *, user_id: str | None = None, role: str | None = None) -> None:
    async with db_module.AsyncSessionLocal() as db:
        db.add(TaskMirror(
            id=uuid.uuid4(),
            document_id=uuid.UUID(pa_id),
            document_type="pa_dir",
            type="approve_pa",
            assigned_user_id=uuid.UUID(user_id) if user_id else None,
            assigned_role=role,
            is_completed=False,
            created_at=datetime.now(timezone.utc),
        ))
        await db.commit()


async def _set_role_management(rm: dict) -> None:
    async with db_module.AsyncSessionLocal() as db:
        existing = (await db.execute(select(EpmsCompanyConfig))).scalars().all()
        if existing:
            for cfg in existing:
                cfg.role_management = rm
        else:
            db.add(EpmsCompanyConfig(
                id=uuid.uuid4(), dept_gm_opm_mapping={}, workflow_defs={}, role_management=rm,
            ))
        await db.commit()


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_sees_is_owner_but_cannot_approve():
    owner_id = str(uuid.uuid4())
    async with _client_for("requester", owner_id) as owner:
        pa = await _make_pa(owner)
        await _set_pa_status(pa["id"], "submitted")
        # Even with an open task wrongly pointing at the owner, owners never approve.
        await _add_open_task(pa["id"], user_id=owner_id)
        resp = await owner.get(f"/api/v1/pa/{pa['id']}/permissions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_owner"] is True
    assert body["can_approve"] is False
    assert body["can_pay"] is False


@pytest.mark.asyncio
async def test_assigned_task_user_can_approve():
    """A user with an open task assigned directly to them may approve — regardless
    of JWT role (approval roles are assignments, not JWT claims)."""
    approver_id = str(uuid.uuid4())
    async with _client_for("requester", str(uuid.uuid4())) as owner:
        pa = await _make_pa(owner)
    await _set_pa_status(pa["id"], "submitted")
    await _add_open_task(pa["id"], user_id=approver_id)
    async with _client_for("requester", approver_id) as approver:
        resp = await approver.get(f"/api/v1/pa/{pa['id']}/permissions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_owner"] is False
    assert body["can_approve"] is True


@pytest.mark.asyncio
async def test_role_task_resolved_via_role_management():
    """An open role-based task (assigned_user_id NULL, assigned_role=finance_bp) is
    resolved against company_config.role_management.finance_bp_user_ids."""
    bp_id = str(uuid.uuid4())
    async with _client_for("requester", str(uuid.uuid4())) as owner:
        pa = await _make_pa(owner)
    await _set_pa_status(pa["id"], "in_review")
    await _add_open_task(pa["id"], role="finance_bp")
    await _set_role_management({"finance_bp_user_ids": [bp_id]})
    async with _client_for("requester", bp_id) as bp:
        resp = await bp.get(f"/api/v1/pa/{pa['id']}/permissions")
    assert resp.status_code == 200
    assert resp.json()["can_approve"] is True


@pytest.mark.asyncio
async def test_unrelated_user_cannot_approve():
    async with _client_for("requester", str(uuid.uuid4())) as owner:
        pa = await _make_pa(owner)
    await _set_pa_status(pa["id"], "submitted")
    await _set_role_management({})
    async with _client_for("requester", str(uuid.uuid4())) as outsider:
        resp = await outsider.get(f"/api/v1/pa/{pa['id']}/permissions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_owner"] is False
    assert body["can_approve"] is False


@pytest.mark.asyncio
async def test_admin_can_approve_pending_pa(admin_client):
    async with _client_for("requester", str(uuid.uuid4())) as owner:
        pa = await _make_pa(owner)
    await _set_pa_status(pa["id"], "submitted")
    resp = await admin_client.get(f"/api/v1/pa/{pa['id']}/permissions")
    assert resp.status_code == 200
    assert resp.json()["can_approve"] is True


@pytest.mark.asyncio
async def test_can_pay_only_on_approved_for_finance(finance_client):
    async with _client_for("requester", str(uuid.uuid4())) as owner:
        pa = await _make_pa(owner)

    # Not approved yet → no pay
    resp = await finance_client.get(f"/api/v1/pa/{pa['id']}/permissions")
    assert resp.status_code == 200
    assert resp.json()["can_pay"] is False

    await _set_pa_status(pa["id"], "approved")
    resp = await finance_client.get(f"/api/v1/pa/{pa['id']}/permissions")
    assert resp.json()["can_pay"] is True

    async with _client_for("requester", str(uuid.uuid4())) as outsider:
        resp = await outsider.get(f"/api/v1/pa/{pa['id']}/permissions")
    assert resp.json()["can_pay"] is False


@pytest.mark.asyncio
async def test_permissions_pa_not_found(admin_client):
    resp = await admin_client.get(f"/api/v1/pa/{uuid.uuid4()}/permissions")
    assert resp.status_code == 404
