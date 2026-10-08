"""OA's Direct PA is retired: hidden in the UI, and refused at the API.

Product decision (2026-08-07, confirmed 2026-09-11): the feature is withdrawn,
but the code stays in the tree rather than being deleted. Hiding the buttons is
not enough on its own — without this gate a caller with a token could still
mint PA-DIRs that nothing in OA can then show them.

What must KEEP working is everything around it: reading, approving, paying and
the by-po lookup all still serve whatever records exist, so nothing already in
flight is stranded, and the creation path itself stays under test (see the
`_direct_pa_switched_on` fixture in the other PA modules) so the feature can be
switched back on rather than rebuilt.
"""
import uuid

import pytest

from app.models.pa import PaymentApplication


async def _reviewed_invoice(client) -> dict:
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


def _payload(invoice_id: str) -> dict:
    return {
        "invoice_id": invoice_id,
        "vendor_name": "Titan Power Ltd",
        "payment_amount": "1130.00",
        "currency": "CAD",
    }


# ── The gate ──────────────────────────────────────────────────────────────────

async def test_creating_a_direct_pa_is_gone(admin_client):
    inv = await _reviewed_invoice(admin_client)

    resp = await admin_client.post("/api/v1/pa/direct", json=_payload(inv["id"]))

    assert resp.status_code == 410
    assert "discontinued" in resp.json()["detail"].lower()


async def test_not_even_an_admin_can_create_one(admin_client, finance_client):
    """No role carves an exception — the feature is withdrawn, not restricted."""
    inv = await _reviewed_invoice(admin_client)

    resp = await finance_client.post("/api/v1/pa/direct", json=_payload(inv["id"]))

    assert resp.status_code == 410


async def test_the_invoice_is_left_untouched_by_a_refused_creation(admin_client):
    """The 410 fires before anything is written — the invoice must not be
    marked `used` by an attempt that created no PA."""
    inv = await _reviewed_invoice(admin_client)

    await admin_client.post("/api/v1/pa/direct", json=_payload(inv["id"]))

    after = await admin_client.get(f"/api/v1/invoices/{inv['id']}")
    assert after.json()["status"] == "reviewed"
    assert after.json()["pa_id"] is None


# ── What must still work ──────────────────────────────────────────────────────

@pytest.fixture
async def existing_pa(db_session) -> PaymentApplication:
    """A PA-DIR that predates the retirement, written straight to the table."""
    pa = PaymentApplication(
        id=uuid.uuid4(),
        pa_number=f"PA-LEGACY-{uuid.uuid4().hex[:6]}",
        title="Direct Payment — Titan Power Ltd",
        vendor_id=uuid.uuid4(), vendor_name="Titan Power Ltd",
        invoice_ids=[], gr_ids=[], pa_type="PA-DIR",
        subtotal=1130, payment_amount=1130, currency="CAD",
        status="submitted", approval_step_idx=0,
        created_by=uuid.uuid4(),
    )
    db_session.add(pa)
    await db_session.commit()
    return pa


async def test_an_existing_direct_pa_can_still_be_read(admin_client, existing_pa):
    resp = await admin_client.get(f"/api/v1/pa/{existing_pa.id}")

    assert resp.status_code == 200
    assert resp.json()["pa_number"] == existing_pa.pa_number


async def test_an_existing_direct_pa_can_still_be_acted_on(admin_client, existing_pa, mocker):
    """Approve / return / reject still reach the engine, so nothing in flight
    is stranded by the retirement."""
    delegate = mocker.patch("app.api.v1.pa.delegate_action",
                            new_callable=mocker.AsyncMock, return_value=None)

    resp = await admin_client.post(
        f"/api/v1/pa/{existing_pa.id}/action", json={"action": "approve"})

    assert resp.status_code == 200
    delegate.assert_called_once()
    assert delegate.call_args[0][0] == "pa_dir"


async def test_the_pa_list_still_answers(admin_client, existing_pa):
    resp = await admin_client.get("/api/v1/pa")

    assert resp.status_code == 200
    assert str(existing_pa.id) in {p["id"] for p in resp.json()["items"]}


async def test_the_by_po_lookup_still_answers(admin_client):
    """EPMS's document chain tree reads this one — it must not go away with
    the OA feature."""
    resp = await admin_client.get(f"/api/v1/pa/by-po/{uuid.uuid4()}")

    assert resp.status_code == 200
    assert resp.json()["items"] == []
