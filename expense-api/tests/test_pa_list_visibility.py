"""GET /pa list — visibility for all parties on a Direct PA's workflow.

Requirement: every person related to an OA PA must see it in the list — the
Requester (creator) AND any Approver (e.g. Department Manager) who has acted on
it. The old rule showed a PA only to its creator, so an approver's list went
empty the moment they were done. Mirrors the expense-claim list contract.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

import app.db.base as db_module
from app.core.config import settings
from app.main import create_app
from app.models.approval_event_mirror import ApprovalEventMirror
from app.models.pa import PaymentApplication


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


async def _add_approval_event(pa_id: str, pa_number: str, actor_id: str, action: str) -> None:
    async with db_module.AsyncSessionLocal() as db:
        db.add(ApprovalEventMirror(
            id=uuid.uuid4(),
            document_type="pa_dir",
            document_id=uuid.UUID(pa_id),
            document_number=pa_number,
            step_idx=0,
            action=action,
            actor_id=uuid.UUID(actor_id),
            actor_role="dept_manager",
            created_at=datetime.now(timezone.utc),
        ))
        await db.commit()


def _ids(body: dict) -> set[str]:
    return {item["id"] for item in body["items"]}


@pytest.mark.asyncio
async def test_requester_sees_own_pa():
    owner_id = str(uuid.uuid4())
    async with _client_for("requester", owner_id) as owner:
        pa = await _make_pa(owner)
        await _set_pa_status(pa["id"], "submitted")
        resp = await owner.get("/api/v1/pa")
    assert resp.status_code == 200
    assert pa["id"] in _ids(resp.json())


@pytest.mark.asyncio
async def test_approver_who_acted_sees_pa():
    """A Department Manager who approved a PA they did not create must still see
    it in the list — previously the list filtered on created_by only."""
    approver_id = str(uuid.uuid4())
    async with _client_for("requester", str(uuid.uuid4())) as owner:
        pa = await _make_pa(owner)
    await _set_pa_status(pa["id"], "in_review")
    await _add_approval_event(pa["id"], pa["pa_number"], approver_id, "approve")
    async with _client_for("dept_manager", approver_id) as approver:
        resp = await approver.get("/api/v1/pa")
    assert resp.status_code == 200
    assert pa["id"] in _ids(resp.json())


@pytest.mark.asyncio
async def test_unrelated_user_does_not_see_pa():
    async with _client_for("requester", str(uuid.uuid4())) as owner:
        pa = await _make_pa(owner)
    await _set_pa_status(pa["id"], "submitted")
    async with _client_for("requester", str(uuid.uuid4())) as outsider:
        resp = await outsider.get("/api/v1/pa")
    assert resp.status_code == 200
    assert pa["id"] not in _ids(resp.json())
