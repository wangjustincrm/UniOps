"""PRD-OA §3 — Direct Payment Application (PA-DIR) creation and lifecycle.

PA actions (submit/approve) delegate to approval-api — those calls are mocked
so tests run without a live approval-api. Status transitions are tested by
directly setting PA.status in the test DB.
"""
import uuid
import re
import pytest

import app.db.base as db_module
from app.models.invoice import ExpenseInvoice
from app.models.pa import PaymentApplication


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_reviewed_invoice(client) -> dict:
    resp = await client.post(
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
    )
    assert resp.status_code == 201
    return resp.json()


def _pa_payload(invoice_id: str, **overrides) -> dict:
    base = {
        "invoice_id": invoice_id,
        "vendor_name": "Titan Power Ltd",
        "payment_amount": "1130.00",
        "currency": "CAD",
    }
    base.update(overrides)
    return base


# ── Creation ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_direct_pa(admin_client):
    """PRD-OA §3 — POST /pa/direct creates a PA-DIR in draft status."""
    inv = await _make_reviewed_invoice(admin_client)
    resp = await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))
    assert resp.status_code == 201
    data = resp.json()
    assert data["pa_type"] == "PA-DIR"
    assert data["status"] == "draft"
    assert data["po_id"] is None


@pytest.mark.asyncio
async def test_pa_number_format(admin_client):
    """PRD-OA — PA number follows PA-YYYYMMDD-XXXX format."""
    inv = await _make_reviewed_invoice(admin_client)
    resp = await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))
    assert resp.status_code == 201
    assert re.match(r"PA-\d{8}-\d{4}", resp.json()["pa_number"])


@pytest.mark.asyncio
async def test_pa_auto_title(admin_client):
    """PRD-OA §3.1 — title auto-generated as 'Direct Payment — {Vendor} {InvNum}'."""
    inv = await _make_reviewed_invoice(admin_client)
    resp = await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))
    assert resp.status_code == 201
    assert "Direct Payment" in resp.json()["title"]


@pytest.mark.asyncio
async def test_pa_custom_title(admin_client):
    """PRD-OA §3.1 — user-provided title overrides the auto-generated one."""
    inv = await _make_reviewed_invoice(admin_client)
    resp = await admin_client.post(
        "/api/v1/pa/direct",
        json=_pa_payload(inv["id"], title="Q2 Licensing Fee"),
    )
    assert resp.status_code == 201
    assert resp.json()["title"] == "Q2 Licensing Fee"


@pytest.mark.asyncio
async def test_pa_with_budget_account_code(admin_client):
    """PRD-OA §3.2 — budget_account_code stored on PA."""
    inv = await _make_reviewed_invoice(admin_client)
    resp = await admin_client.post(
        "/api/v1/pa/direct",
        json=_pa_payload(inv["id"], budget_account_code="GA00101"),
    )
    assert resp.status_code == 201
    assert resp.json()["budget_account_code"] == "GA00101"


@pytest.mark.asyncio
async def test_invoice_marked_used_after_pa_creation(admin_client):
    """After PA-DIR is created, invoice.status becomes 'used'."""
    inv = await _make_reviewed_invoice(admin_client)
    await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))

    async with db_module.AsyncSessionLocal() as db:
        invoice = await db.get(ExpenseInvoice, uuid.UUID(inv["id"]))
        assert invoice.status == "used"


@pytest.mark.asyncio
async def test_cannot_create_pa_for_used_invoice(admin_client):
    """PRD-OA — once an invoice is used, a second PA-DIR cannot be created for it."""
    inv = await _make_reviewed_invoice(admin_client)
    r1 = await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))
    assert r1.status_code == 201

    r2 = await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_cannot_create_pa_for_nonexistent_invoice(admin_client):
    resp = await admin_client.post(
        "/api/v1/pa/direct",
        json=_pa_payload(str(uuid.uuid4())),
    )
    assert resp.status_code == 404


# ── Read ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_pas(admin_client):
    resp = await admin_client.get("/api/v1/pa")
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_get_pa(admin_client):
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    resp = await admin_client.get(f"/api/v1/pa/{pa['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == pa["id"]


@pytest.mark.asyncio
async def test_get_pa_not_found(admin_client):
    resp = await admin_client.get(f"/api/v1/pa/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── Action delegation (mocked) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pa_action_submit_delegates_to_approval_api(admin_client, mocker):
    """POST /pa/{id}/action calls delegate_action with action_key='pa_dir'."""
    mock_delegate = mocker.patch(
        "app.api.v1.pa.delegate_action",
        new_callable=mocker.AsyncMock,
        return_value=None,
    )
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/action",
        json={"action": "submit"},
    )
    assert resp.status_code == 200
    mock_delegate.assert_called_once()
    assert mock_delegate.call_args[0][0] == "pa_dir"
    assert mock_delegate.call_args[0][2] == "submit"


# ── Payment recording ──────────────────────────────────────────────────────────

# Phase 0-B1.5: /pay forwards to finance-api's unified payment executor —
# status/permission judgments happen there. These tests mock the client and
# assert the forward + error mapping. The executor itself is tested in
# finance-api/tests/test_payment_execute.py.

def _mock_finance(mocker, **kwargs):
    return mocker.patch(
        "app.api.v1.pa.finance_client.execute_payment",
        new_callable=mocker.AsyncMock,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_record_payment_maps_409_from_finance(admin_client, mocker):
    mock_exec = _mock_finance(mocker, side_effect=ValueError("Cannot pay PA in status 'draft'"))
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/pay",
        json={"bank_account_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 409
    mock_exec.assert_called_once()


@pytest.mark.asyncio
async def test_record_payment_maps_403_from_finance(admin_client, mocker):
    _mock_finance(mocker, side_effect=PermissionError("Insufficient role"))
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/pay",
        json={"bank_account_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_record_payment_forwards_to_finance(admin_client, mocker):
    """The forward carries doc_kind=pa_dir and the chosen bank_account_id."""
    mock_exec = _mock_finance(mocker, return_value={"new_status": "processed"})
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    bank_id = str(uuid.uuid4())
    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/pay",
        json={"bank_account_id": bank_id},
    )
    assert resp.status_code == 200
    mock_exec.assert_called_once()
    kw = mock_exec.call_args.kwargs
    assert kw["doc_kind"] == "pa_dir"
    assert str(kw["doc_id"]) == pa["id"]
    assert str(kw["bank_account_id"]) == bank_id


@pytest.mark.asyncio
async def test_pa_action_rejects_process(admin_client, mocker):
    """Payment is not a workflow action: OA's only payment entry is /pay
    (which forwards to finance-api's unified executor). action=process is
    rejected at the schema level so no second payment path can exist."""
    mock_delegate = mocker.patch(
        "app.api.v1.pa.delegate_action", new_callable=mocker.AsyncMock,
    )
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/action", json={"action": "process"},
    )
    assert resp.status_code == 422
    mock_delegate.assert_not_called()


# ── Draft edit (PATCH /pa/{id}) ──────────────────────────────────────────────

async def _set_pa_status(pa_id: str, status: str, pa_type: str | None = None):
    """Directly set a PA's status (and optionally pa_type) in the test DB."""
    async with db_module.AsyncSessionLocal() as db:
        pa = await db.get(PaymentApplication, uuid.UUID(pa_id))
        pa.status = status
        if pa_type is not None:
            pa.pa_type = pa_type
        await db.commit()


@pytest.mark.asyncio
async def test_patch_draft_pa_updates_fields(requester_client):
    inv = await _make_reviewed_invoice(requester_client)
    pa = (await requester_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    resp = await requester_client.patch(
        f"/api/v1/pa/{pa['id']}",
        json={"title": "Edited Title", "notes": "edited note"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Edited Title"
    assert body["notes"] == "edited note"


@pytest.mark.asyncio
async def test_patch_returned_pa_allowed(requester_client):
    inv = await _make_reviewed_invoice(requester_client)
    pa = (await requester_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    await _set_pa_status(pa["id"], "returned")
    resp = await requester_client.patch(f"/api/v1/pa/{pa['id']}", json={"title": "After Return"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "After Return"


@pytest.mark.asyncio
async def test_patch_submitted_pa_rejected(requester_client):
    inv = await _make_reviewed_invoice(requester_client)
    pa = (await requester_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    await _set_pa_status(pa["id"], "submitted")
    resp = await requester_client.patch(f"/api/v1/pa/{pa['id']}", json={"title": "Nope"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_patch_pa_po_rejected(requester_client):
    inv = await _make_reviewed_invoice(requester_client)
    pa = (await requester_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    await _set_pa_status(pa["id"], "draft", pa_type="PA-PO")
    resp = await requester_client.patch(f"/api/v1/pa/{pa['id']}", json={"title": "Nope"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_patch_non_owner_rejected(requester_client, requester_client_b):
    inv = await _make_reviewed_invoice(requester_client)
    pa = (await requester_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    resp = await requester_client_b.patch(f"/api/v1/pa/{pa['id']}", json={"title": "Hijack"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patch_pa_not_found(requester_client):
    resp = await requester_client.patch(f"/api/v1/pa/{uuid.uuid4()}", json={"title": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pa_response_exposes_cost_center_id(requester_client):
    inv = await _make_reviewed_invoice(requester_client)
    cc_id = str(uuid.uuid4())
    pa = (await requester_client.post(
        "/api/v1/pa/direct", json=_pa_payload(inv["id"], cost_center_id=cc_id),
    )).json()
    assert pa["cost_center_id"] == cc_id
