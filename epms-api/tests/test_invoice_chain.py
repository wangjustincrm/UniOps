"""GET /invoices/{id}/chain — the four-step document chain behind the
Invoice List's Due Date drawer (Match PO → Link GR → Create PA → Payment).

The chain endpoint is a PROJECTION of state other endpoints write, so these
tests set that state directly through the ORM rather than driving the whole
match/receive/pay workflow over HTTP. Driving it would drag in approval-api,
which is not reachable from the local test stack (every such test in
test_invoices.py fails on `401 Invalid token` today) and would prove nothing
extra about the projection itself.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder

INV_URL = "/api/v1/invoices"
VENDOR_URL = "/api/v1/vendors"
PO_URL = "/api/v1/po"

_PO_LINE = {"description": "Widget", "qty": "10", "unit": "EA", "unit_price": "100.00"}


async def _make_vendor(client, code):
    r = await client.post(VENDOR_URL, json={
        "code": code, "name": "Chain Vendor", "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_po(client, vendor_id):
    r = await client.post(PO_URL, json={
        "title": "Chain Test PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "line_items": [_PO_LINE],
    })
    r.raise_for_status()
    return r.json()


async def _make_invoice(client, vendor_id, **overrides):
    body = {
        "vendor_id": vendor_id,
        "vendor_invoice_number": f"VI-{uuid.uuid4().hex[:8]}",
        "amount": "1000.00", "tax_amount": "130.00", "currency": "CAD",
        "invoice_date": "2026-03-20", "due_date": "2026-04-19",
    }
    body.update(overrides)
    r = await client.post(INV_URL, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _sf(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _match_to_po(test_engine, invoice_id, po):
    """Write the PO-route match fields the matching endpoint would write."""
    async with _sf(test_engine)() as db:
        inv = (await db.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(invoice_id)))).scalar_one()
        inv.po_id = uuid.UUID(po["id"])
        inv.po_number = po["number"]
        inv.match_route = "po"
        inv.status = "matched"
        await db.commit()


async def _link_gr(test_engine, invoice_id, po, number_suffix="01"):
    """Create a GR against the PO and link it onto the invoice."""
    async with _sf(test_engine)() as db:
        po_row = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == uuid.UUID(po["id"])))).scalar_one()
        gr = GoodsReceipt(
            id=uuid.uuid4(), number=f"GR-CHAIN-{uuid.uuid4().hex[:6]}-{number_suffix}",
            title="Chain GR", po_id=po_row.id, po_number=po_row.number,
            vendor_id=po_row.vendor_id, vendor_name=po_row.vendor_name,
            gr_type="physical", procurement_type=2, currency="CAD",
            status="confirmed", created_by=po_row.created_by,
        )
        db.add(gr)
        await db.flush()
        inv = (await db.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(invoice_id)))).scalar_one()
        inv.gr_id = gr.id
        inv.gr_number = gr.number
        inv.gr_ids = [str(gr.id)]
        await db.commit()
        return {"id": str(gr.id), "number": gr.number}


async def _make_pa(test_engine, invoice_id, po, status="draft", paid=False):
    async with _sf(test_engine)() as db:
        po_row = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == uuid.UUID(po["id"])))).scalar_one()
        pa = PaymentApplication(
            id=uuid.uuid4(), pa_number=f"PA-CHAIN-{uuid.uuid4().hex[:8]}",
            title="Chain PA", po_id=po_row.id, po_number=po_row.number,
            vendor_id=po_row.vendor_id, vendor_name=po_row.vendor_name,
            invoice_ids=[invoice_id], gr_ids=[],
            subtotal=Decimal("1000.00"), payment_amount=Decimal("1130.00"),
            tax_amount=Decimal("130.00"), currency="CAD", status=status,
            created_by=po_row.created_by,
        )
        if paid:
            from datetime import datetime, timezone
            pa.paid_at = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        db.add(pa)
        await db.commit()
        return {"id": str(pa.id), "pa_number": pa.pa_number}


def _steps(payload):
    return {s["key"]: s for s in payload["steps"]}


# ── The four steps ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unmatched_invoice_reports_every_step_outstanding(admin_client):
    v = await _make_vendor(admin_client, f"VND-CHAIN-{uuid.uuid4().hex[:6]}")
    inv = await _make_invoice(admin_client, v["id"])

    r = await admin_client.get(f"{INV_URL}/{inv['id']}/chain")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invoice_id"] == inv["id"]
    assert body["internal_ref"] == inv["internal_ref"]
    assert body["due_date"] == "2026-04-19"

    steps = _steps(body)
    assert [s["key"] for s in body["steps"]] == ["match_po", "link_gr", "create_pa", "payment"]
    assert steps["match_po"]["state"] == "pending"
    # Nothing downstream can even be attempted before the invoice is matched.
    assert steps["link_gr"]["state"] == "blocked"
    assert steps["create_pa"]["state"] == "blocked"
    assert steps["payment"]["state"] == "blocked"


@pytest.mark.asyncio
async def test_po_matched_invoice_names_the_po_and_awaits_a_gr(admin_client, test_engine):
    v = await _make_vendor(admin_client, f"VND-CHAIN-{uuid.uuid4().hex[:6]}")
    po = await _make_po(admin_client, v["id"])
    inv = await _make_invoice(admin_client, v["id"])
    await _match_to_po(test_engine, inv["id"], po)

    steps = _steps((await admin_client.get(f"{INV_URL}/{inv['id']}/chain")).json())
    assert steps["match_po"]["state"] == "done"
    assert steps["match_po"]["refs"] == [
        {"doc_type": "po", "id": po["id"], "number": po["number"]}
    ]
    assert steps["link_gr"]["state"] == "pending"
    # The receipt gate means a PA cannot be raised until a GR is linked.
    assert steps["create_pa"]["state"] == "blocked"


@pytest.mark.asyncio
async def test_linked_gr_is_reported_with_its_number(admin_client, test_engine):
    v = await _make_vendor(admin_client, f"VND-CHAIN-{uuid.uuid4().hex[:6]}")
    po = await _make_po(admin_client, v["id"])
    inv = await _make_invoice(admin_client, v["id"])
    await _match_to_po(test_engine, inv["id"], po)
    gr = await _link_gr(test_engine, inv["id"], po)

    steps = _steps((await admin_client.get(f"{INV_URL}/{inv['id']}/chain")).json())
    assert steps["link_gr"]["state"] == "done"
    assert steps["link_gr"]["refs"] == [
        {"doc_type": "gr", "id": gr["id"], "number": gr["number"]}
    ]
    # Fully matched and received: raising the PA is now the outstanding action.
    assert steps["create_pa"]["state"] == "pending"
    assert steps["payment"]["state"] == "blocked"


@pytest.mark.asyncio
async def test_existing_pa_is_reported_with_its_number_and_status(admin_client, test_engine):
    v = await _make_vendor(admin_client, f"VND-CHAIN-{uuid.uuid4().hex[:6]}")
    po = await _make_po(admin_client, v["id"])
    inv = await _make_invoice(admin_client, v["id"])
    await _match_to_po(test_engine, inv["id"], po)
    await _link_gr(test_engine, inv["id"], po)
    pa = await _make_pa(test_engine, inv["id"], po, status="in_review")

    steps = _steps((await admin_client.get(f"{INV_URL}/{inv['id']}/chain")).json())
    assert steps["create_pa"]["state"] == "done"
    assert steps["create_pa"]["refs"] == [
        {"doc_type": "pa", "id": pa["id"], "number": pa["pa_number"]}
    ]
    assert steps["create_pa"]["detail"] == "in_review"
    # Payment cannot start while the PA is still working through approval.
    assert steps["payment"]["state"] == "blocked"


@pytest.mark.asyncio
async def test_approved_pa_leaves_payment_outstanding(admin_client, test_engine):
    v = await _make_vendor(admin_client, f"VND-CHAIN-{uuid.uuid4().hex[:6]}")
    po = await _make_po(admin_client, v["id"])
    inv = await _make_invoice(admin_client, v["id"])
    await _match_to_po(test_engine, inv["id"], po)
    await _link_gr(test_engine, inv["id"], po)
    await _make_pa(test_engine, inv["id"], po, status="approved")

    steps = _steps((await admin_client.get(f"{INV_URL}/{inv['id']}/chain")).json())
    assert steps["payment"]["state"] == "pending"


@pytest.mark.asyncio
async def test_paid_pa_completes_the_chain(admin_client, test_engine):
    v = await _make_vendor(admin_client, f"VND-CHAIN-{uuid.uuid4().hex[:6]}")
    po = await _make_po(admin_client, v["id"])
    inv = await _make_invoice(admin_client, v["id"])
    await _match_to_po(test_engine, inv["id"], po)
    await _link_gr(test_engine, inv["id"], po)
    await _make_pa(test_engine, inv["id"], po, status="processed", paid=True)

    steps = _steps((await admin_client.get(f"{INV_URL}/{inv['id']}/chain")).json())
    assert steps["payment"]["state"] == "done"
    assert steps["payment"]["detail"].startswith("2026-05-01")


@pytest.mark.asyncio
async def test_cancelled_pa_does_not_count_as_a_raised_pa(admin_client, test_engine):
    """A cancelled PA releases its claim on the invoice — the outstanding
    action is 'raise a PA', not 'wait for that one'."""
    v = await _make_vendor(admin_client, f"VND-CHAIN-{uuid.uuid4().hex[:6]}")
    po = await _make_po(admin_client, v["id"])
    inv = await _make_invoice(admin_client, v["id"])
    await _match_to_po(test_engine, inv["id"], po)
    await _link_gr(test_engine, inv["id"], po)
    await _make_pa(test_engine, inv["id"], po, status="cancelled")

    steps = _steps((await admin_client.get(f"{INV_URL}/{inv['id']}/chain")).json())
    assert steps["create_pa"]["state"] == "pending"
    assert steps["create_pa"]["refs"] == []


@pytest.mark.asyncio
async def test_agreement_route_invoice_has_no_gr_step(admin_client, test_engine):
    """House-account/recurring invoices settle against agreement receipts, not
    GRs — the GR step is not merely outstanding, it does not apply."""
    from app.models.agreement import PurchaseAgreement

    v = await _make_vendor(admin_client, f"VND-CHAIN-{uuid.uuid4().hex[:6]}")
    inv = await _make_invoice(admin_client, v["id"])
    async with _sf(test_engine)() as db:
        row = (await db.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(inv["id"])))).scalar_one()
        agr = PurchaseAgreement(
            id=uuid.uuid4(), number=f"AGR-CHAIN-{uuid.uuid4().hex[:6]}",
            title="House account", agreement_type="house_account",
            vendor_id=row.vendor_id, vendor_name=row.vendor_name,
            valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
            created_by=row.uploaded_by,
        )
        db.add(agr)
        await db.flush()
        row.agreement_id = agr.id
        row.agreement_number = agr.number
        row.agreement_type = "house_account"
        row.match_route = "agreement"
        row.status = "matched"
        await db.commit()
        agr_number = agr.number
        agr_id = str(agr.id)

    steps = _steps((await admin_client.get(f"{INV_URL}/{inv['id']}/chain")).json())
    assert steps["match_po"]["state"] == "done"
    assert steps["match_po"]["refs"] == [
        {"doc_type": "agreement", "id": agr_id, "number": agr_number}
    ]
    assert steps["link_gr"]["state"] == "not_applicable"
    # No receipt gate to clear on this route — the PA is the next real action.
    assert steps["create_pa"]["state"] == "pending"


# ── Access control ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chain_404s_for_an_invoice_outside_the_callers_scope(
    admin_client, requester_client
):
    v = await _make_vendor(admin_client, f"VND-CHAIN-{uuid.uuid4().hex[:6]}")
    inv = await _make_invoice(admin_client, v["id"])

    # Assert the reachable case FIRST: a plain "404" is also what a missing
    # route returns, so without this the scope assertion below would pass
    # against an endpoint that does not exist at all.
    assert (await admin_client.get(f"{INV_URL}/{inv['id']}/chain")).status_code == 200

    r = await requester_client.get(f"{INV_URL}/{inv['id']}/chain")
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_pa_and_payment_are_withheld_without_view_pa(test_engine, admin_client):
    """The drawer must not become a side channel onto payment state for a role
    the Access Control Matrix keeps out of the PA module."""
    import uuid as _uuid

    from app.core.security import create_access_token
    from app.crud import user as user_crud
    from app.main import create_app
    from app.schemas.auth import RegisterRequest
    from httpx import ASGITransport, AsyncClient

    v = await _make_vendor(admin_client, f"VND-CHAIN-{_uuid.uuid4().hex[:6]}")
    po = await _make_po(admin_client, v["id"])
    inv = await _make_invoice(admin_client, v["id"])
    await _match_to_po(test_engine, inv["id"], po)
    await _link_gr(test_engine, inv["id"], po)
    await _make_pa(test_engine, inv["id"], po, status="approved")

    async with _sf(test_engine)() as db:
        auditor = await user_crud.create(db, RegisterRequest(
            email=f"chain-auditor-{_uuid.uuid4().hex[:8]}@example.com",
            password="TestPass1!", full_name="Chain Auditor", role="auditor"))
        await db.commit()
        # auditor sees invoices but, with view_pa revoked, must not see the PA.
        await db.execute(text(
            "DELETE FROM role_permissions "
            "WHERE role_code='auditor' AND permission_key='view_pa'"))
        await db.commit()
        uid = auditor.id

    token = create_access_token(str(uid), "auditor")
    try:
        async with AsyncClient(transport=ASGITransport(app=create_app()),
                               base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as c:
            r = await c.get(f"{INV_URL}/{inv['id']}/chain")
        assert r.status_code == 200, r.text
        steps = _steps(r.json())
        assert steps["match_po"]["state"] == "done"
        assert steps["create_pa"]["state"] == "restricted"
        assert steps["create_pa"]["refs"] == []
        assert steps["payment"]["state"] == "restricted"
    finally:
        async with _sf(test_engine)() as db:
            await db.execute(text(
                "INSERT INTO role_permissions (role_code, permission_key) "
                "VALUES ('auditor','view_pa') ON CONFLICT DO NOTHING"))
            await db.commit()
