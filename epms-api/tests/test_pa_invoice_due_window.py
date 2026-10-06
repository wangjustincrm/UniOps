"""Several invoices may share one payment application only if they fall due
within a week of each other.

One PA is one transfer on one payment date, so bundling invoices that fall due
weeks apart either pays the late ones early or the early ones late. Checked on
create and on PATCH of the invoice list, so editing cannot assemble what
creation refuses. The window is also stated to the assistant
(knowledge/pa_types.yaml) — that statement is pinned to the constant here.
"""
import re
import uuid as _uuid
from datetime import date as _date, timedelta as _td
from decimal import Decimal as _Decimal
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.pa import PA_INVOICE_DUE_WINDOW_DAYS

PA_URL = "/api/v1/pa"
PO_URL = "/api/v1/po"
VENDOR_URL = "/api/v1/vendors"

_BASE_DUE = _date(2026, 3, 2)


async def _po_with_invoices(client, test_engine, due_dates):
    """One PO that passes the receipt gate, with one matched invoice per due date."""
    from app.crud import user as user_crud
    from app.models.gr import GoodsReceipt
    from app.models.invoice import Invoice
    from app.schemas.auth import RegisterRequest

    v = (await client.post(VENDOR_URL, json={
        "code": f"VND-DUE-{_uuid.uuid4().hex[:8].upper()}", "name": "Due Window Vendor",
        "category": "Parts", "contact_name": "V", "contact_email": "v@v.com",
        "payment_terms": "net30", "currency": "CAD",
    })).json()
    r = await client.post(PO_URL, json={
        "title": "Due Window PO", "type": 2, "vendor_id": v["id"], "currency": "CAD",
        "tax_rate": "0.13",
        "line_items": [{"description": "Part", "qty": "5", "unit": "EA", "unit_price": "80.00"}],
    })
    r.raise_for_status()
    po = r.json()

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"due-{_uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Due Window Tester", role="warehouse_staff",
        ))
        gr = GoodsReceipt(
            number=f"GR-{_uuid.uuid4().hex[:8]}", title="Test GR",
            po_id=_uuid.UUID(po["id"]), po_number=po["number"],
            vendor_id=_uuid.UUID(v["id"]), vendor_name="Due Window Vendor",
            gr_type="standard", procurement_type=1, currency="CAD",
            status="pending_ack", created_by=user.id,
        )
        db.add(gr)
        await db.flush()
        inv_ids = []
        for due in due_dates:
            inv = Invoice(
                internal_ref=f"I-{_uuid.uuid4().hex[:6]}",
                vendor_invoice_number=f"I-{_uuid.uuid4().hex[:6]}",
                vendor_id=_uuid.UUID(v["id"]), vendor_name="Due Window Vendor",
                amount=_Decimal("100"), tax_amount=_Decimal("13"), total_amount=_Decimal("113"),
                invoice_date=_date(2026, 1, 1), due_date=due,
                status="matched", line_items=[],
                po_id=_uuid.UUID(po["id"]), gr_id=gr.id, uploaded_by=user.id,
            )
            db.add(inv)
            await db.flush()
            inv_ids.append(str(inv.id))
        await db.commit()
    return po, inv_ids


def _payload(po_id, invoice_ids):
    n = len(invoice_ids)
    return {
        "po_id": po_id, "title": "Due Window PA", "pa_type": "regular",
        "subtotal": f"{100 * n}.00", "tax_amount": f"{13 * n}.00", "currency": "CAD",
        "invoice_ids": invoice_ids,
    }


@pytest.mark.asyncio
async def test_invoices_due_exactly_a_week_apart_accepted(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [
        _BASE_DUE, _BASE_DUE + _td(days=3), _BASE_DUE + _td(days=PA_INVOICE_DUE_WINDOW_DAYS),
    ])
    r = await admin_client.post(PA_URL, json=_payload(po["id"], invs))
    assert r.status_code == 201, r.text
    assert sorted(r.json()["invoice_ids"]) == sorted(invs)


@pytest.mark.asyncio
async def test_invoices_due_more_than_a_week_apart_refused(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [
        _BASE_DUE, _BASE_DUE + _td(days=PA_INVOICE_DUE_WINDOW_DAYS + 1),
    ])
    r = await admin_client.post(PA_URL, json=_payload(po["id"], invs))
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "fall due within" in detail
    assert _BASE_DUE.isoformat() in detail
    assert f"{PA_INVOICE_DUE_WINDOW_DAYS + 1} days apart" in detail


@pytest.mark.asyncio
async def test_single_invoice_has_no_window(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [_BASE_DUE])
    r = await admin_client.post(PA_URL, json=_payload(po["id"], invs))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_patch_cannot_add_an_invoice_outside_the_window(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [
        _BASE_DUE, _BASE_DUE + _td(days=2), _BASE_DUE + _td(days=30),
    ])
    r = await admin_client.post(PA_URL, json=_payload(po["id"], invs[:2]))
    assert r.status_code == 201, r.text
    pa_id = r.json()["id"]

    r = await admin_client.patch(f"{PA_URL}/{pa_id}", json={"invoice_ids": invs})
    assert r.status_code == 422, r.text
    assert "fall due within" in r.json()["detail"]

    # Swapping to another in-window set is still fine.
    r = await admin_client.patch(f"{PA_URL}/{pa_id}", json={"invoice_ids": [invs[1]]})
    assert r.status_code == 200, r.text


def test_assistant_states_the_enforced_window():
    """The guide layer tells users the rule in prose; it must quote the number
    the endpoint actually enforces."""
    path = Path(__file__).resolve().parents[1] / "app" / "knowledge" / "pa_types.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    text = " ".join(
        note for v in doc["values"] for note in (v.get("notes") or [])
    )
    stated = re.findall(r"within (\d+) days of each other", text)
    assert stated == [str(PA_INVOICE_DUE_WINDOW_DAYS)], stated
