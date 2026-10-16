"""PA lines derived from the invoices the PA pays (crud/pa_lines.py).

The browser runs the same rule to preview the lines
(epms/src/lib/paLinesFromInvoices.test.ts mirrors these cases); what is stored
is what the server derives, whatever lines the client sent.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

PA_URL = "/api/v1/pa"
PO_URL = "/api/v1/po"
VENDOR_URL = "/api/v1/vendors"

_LINES = [
    {"description": "REDUCING UNION", "qty": "10", "unit": "pcs", "unit_price": "19.42"},
    {"description": "FOLDING KNIFE", "qty": "2", "unit": "pcs", "unit_price": "28.25"},
    {"description": "DRILL BIT", "qty": "10", "unit": "pcs", "unit_price": "3.38"},
]


async def _vendor(client):
    r = await client.post(VENDOR_URL, json={
        "code": f"VND-PAL-{uuid.uuid4().hex[:6]}", "name": "PAL Vendor", "category": "Parts",
        "contact_name": "V", "contact_email": "v@v.com", "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()["id"]


async def _po(client, vendor_id):
    r = await client.post(PO_URL, json={
        "title": "PAL PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "line_items": _LINES,
    })
    r.raise_for_status()
    return r.json()


async def _seed_invoice(test_engine, po, vendor_id, inv_lines, allocs, *, header_po=None):
    """A matched invoice backed by a GR (so the PA receipt gate admits it).

    inv_lines: [(qty, unit_price, line_total, non_po_fee)]
    allocs:    [(index into inv_lines, po_id, po_line_id or None, amount)]
    """
    from app.crud import user as user_crud
    from app.models.gr import GoodsReceipt
    from app.models.invoice import Invoice
    from app.models.invoice_allocation import InvoicePoAllocation
    from app.schemas.auth import RegisterRequest

    head = header_po or po
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"pal-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="PAL", role="warehouse_staff",
        ))
        gr = GoodsReceipt(
            number=f"GR-{uuid.uuid4().hex[:8]}", title="GR", po_id=uuid.UUID(head["id"]),
            po_number=head["number"], vendor_id=uuid.UUID(vendor_id), vendor_name="PAL Vendor",
            gr_type="standard", procurement_type=1, currency="CAD", status="pending_ack",
            created_by=user.id,
        )
        db.add(gr)
        await db.flush()
        line_ids = [str(uuid.uuid4()) for _ in inv_lines]
        items = [{
            "id": lid, "description": f"inv line {i}", "quantity": str(q), "unit": None,
            "unit_price": str(p), "line_total": str(t), "non_po_fee": fee, "non_po_note": None,
        } for i, (lid, (q, p, t, fee)) in enumerate(zip(line_ids, inv_lines))]
        amount = sum((Decimal(str(t)) for _, _, t, _ in inv_lines), Decimal("0"))
        ref = f"INV-PAL-{uuid.uuid4().hex[:4]}x"
        inv = Invoice(
            internal_ref=ref, vendor_invoice_number="V-123",
            vendor_id=uuid.UUID(vendor_id), vendor_name="PAL Vendor",
            amount=amount, tax_amount=(amount * Decimal("0.13")).quantize(Decimal("0.01")),
            total_amount=(amount * Decimal("1.13")).quantize(Decimal("0.01")),
            invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1), status="matched",
            line_items=items, po_id=uuid.UUID(head["id"]), gr_id=gr.id, uploaded_by=user.id,
        )
        db.add(inv)
        await db.flush()
        for idx, po_id, po_line_id, amt in allocs:
            db.add(InvoicePoAllocation(
                invoice_id=inv.id, invoice_line_id=uuid.UUID(line_ids[idx]),
                po_id=uuid.UUID(po_id), po_line_id=uuid.UUID(po_line_id) if po_line_id else None,
                allocated_amount=Decimal(str(amt)), allocated_tax=Decimal("0"),
                allocated_total=Decimal(str(amt)),
            ))
        await db.commit()
        return str(inv.id), ref


async def _derive(test_engine, invoice_ids, po_ids):
    from app.crud.pa_lines import derive_lines
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        return await derive_lines(db, invoice_ids, [uuid.UUID(p) for p in po_ids])


# ── The rule ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_line_level_allocations_become_lines_worth_the_allocated_amount(admin_client, test_engine):
    vid = await _vendor(admin_client)
    po = await _po(admin_client, vid)
    pl = [l["id"] for l in po["line_items"]]
    inv_id, ref = await _seed_invoice(test_engine, po, vid,
                                      [(10, "19.421", "194.21", False), (2, "28.25", "56.50", False)],
                                      [(0, po["id"], pl[0], "194.21"), (1, po["id"], pl[1], "56.50")])
    out = await _derive(test_engine, [inv_id], [po["id"]])
    assert [(str(l["po_line_id"]), l["description"], l["qty"], l["unit_price"], l["line_total"]) for l in out] == [
        (pl[0], "REDUCING UNION", Decimal("10"), Decimal("19.421"), Decimal("194.21")),
        (pl[1], "FOLDING KNIFE", Decimal("2"), Decimal("28.25"), Decimal("56.50")),
    ]
    assert out[0]["notes"] == f"{ref} · V-123"


@pytest.mark.asyncio
async def test_any_header_level_invoice_means_no_derivation(admin_client, test_engine):
    vid = await _vendor(admin_client)
    po = await _po(admin_client, vid)
    pl = po["line_items"][0]["id"]
    line_inv, _ = await _seed_invoice(test_engine, po, vid, [(1, "5", "5", False)], [(0, po["id"], pl, "5")])
    head_inv, _ = await _seed_invoice(test_engine, po, vid, [(1, "9", "9", False)], [(0, po["id"], None, "9")])
    assert await _derive(test_engine, [line_inv], [po["id"]]) is not None
    assert await _derive(test_engine, [line_inv, head_inv], [po["id"]]) is None


@pytest.mark.asyncio
async def test_split_invoice_gives_each_pa_its_own_share_and_the_fee_to_the_header_po(admin_client, test_engine):
    vid = await _vendor(admin_client)
    po_a = await _po(admin_client, vid)
    po_b = await _po(admin_client, vid)
    inv_id, _ = await _seed_invoice(
        test_engine, po_a, vid,
        [(1, "125", "125", False), (1, "33.79", "33.79", True), (1, "242", "242", False)],
        [(0, po_a["id"], po_a["line_items"][0]["id"], "125"),
         (2, po_b["id"], po_b["line_items"][1]["id"], "242")],
    )
    on_a = await _derive(test_engine, [inv_id], [po_a["id"]])
    assert [(l["po_line_id"] is None, l["line_total"]) for l in on_a] == [
        (False, Decimal("125")), (True, Decimal("33.79"))]
    on_b = await _derive(test_engine, [inv_id], [po_b["id"]])
    assert [l["line_total"] for l in on_b] == [Decimal("242")]


@pytest.mark.asyncio
async def test_fee_only_invoice_derives_on_its_header_po_only(admin_client, test_engine):
    vid = await _vendor(admin_client)
    po_a = await _po(admin_client, vid)
    po_b = await _po(admin_client, vid)
    inv_id, _ = await _seed_invoice(test_engine, po_a, vid, [(1, "40", "40", True)], [])
    out = await _derive(test_engine, [inv_id], [po_a["id"]])
    assert [(l["po_line_id"], l["line_total"]) for l in out] == [(None, Decimal("40.00"))]
    assert await _derive(test_engine, [inv_id], [po_b["id"]]) is None


@pytest.mark.asyncio
async def test_unknown_invoice_id_means_no_derivation(admin_client, test_engine):
    vid = await _vendor(admin_client)
    po = await _po(admin_client, vid)
    assert await _derive(test_engine, [str(uuid.uuid4())], [po["id"]]) is None
    assert await _derive(test_engine, [], [po["id"]]) is None


# ── Through the API ───────────────────────────────────────────────────────────

def _pa_body(po_id, invoice_ids, subtotal, lines):
    return {
        "po_id": po_id, "title": "PAL payment", "pa_type": "regular", "currency": "CAD",
        "subtotal": subtotal, "tax_amount": "0", "invoice_ids": invoice_ids, "line_items": lines,
    }


# What the old picker produced on PA-20260924-0006: every PO line ticked.
_ALL_PO_LINES = [{**l, "qty": l["qty"]} for l in _LINES]


@pytest.mark.asyncio
async def test_create_stores_the_derived_lines_not_the_ticked_ones(admin_client, test_engine):
    vid = await _vendor(admin_client)
    po = await _po(admin_client, vid)
    pl = [l["id"] for l in po["line_items"]]
    inv_id, ref = await _seed_invoice(test_engine, po, vid,
                                      [(10, "19.421", "194.21", False), (2, "28.25", "56.50", False)],
                                      [(0, po["id"], pl[0], "194.21"), (1, po["id"], pl[1], "56.50")])
    r = await admin_client.post(PA_URL, json=_pa_body(po["id"], [inv_id], "250.71", _ALL_PO_LINES))
    assert r.status_code == 201, r.text
    lines = r.json()["line_items"]
    assert [(l["po_line_id"], Decimal(str(l["line_total"]))) for l in lines] == [
        (pl[0], Decimal("194.21")), (pl[1], Decimal("56.50"))]
    assert sum(Decimal(str(l["line_total"])) for l in lines) == Decimal(r.json()["subtotal"])
    assert all(l["notes"].startswith(ref) for l in lines)


@pytest.mark.asyncio
async def test_create_keeps_hand_picked_lines_for_a_header_level_invoice(admin_client, test_engine):
    vid = await _vendor(admin_client)
    po = await _po(admin_client, vid)
    inv_id, _ = await _seed_invoice(test_engine, po, vid, [(1, "251.80", "251.80", False)],
                                    [(0, po["id"], None, "251.80")])
    picked = [_LINES[0], _LINES[1]]
    r = await admin_client.post(PA_URL, json=_pa_body(po["id"], [inv_id], "251.70", picked))
    assert r.status_code == 201, r.text
    assert [l["description"] for l in r.json()["line_items"]] == ["REDUCING UNION", "FOLDING KNIFE"]


@pytest.mark.asyncio
async def test_update_rederives_when_the_invoices_change(admin_client, test_engine):
    vid = await _vendor(admin_client)
    po = await _po(admin_client, vid)
    pl = [l["id"] for l in po["line_items"]]
    inv1, _ = await _seed_invoice(test_engine, po, vid, [(10, "19.42", "194.20", False)],
                                  [(0, po["id"], pl[0], "194.20")])
    inv2, _ = await _seed_invoice(test_engine, po, vid, [(10, "3.38", "33.80", False)],
                                  [(0, po["id"], pl[2], "33.80")])
    r = await admin_client.post(PA_URL, json=_pa_body(po["id"], [inv1], "194.20", _ALL_PO_LINES))
    assert r.status_code == 201, r.text
    pa_id = r.json()["id"]

    r = await admin_client.patch(f"{PA_URL}/{pa_id}", json={"invoice_ids": [inv1, inv2], "subtotal": "228.00"})
    assert r.status_code == 200, r.text
    assert [(l["po_line_id"], Decimal(str(l["line_total"]))) for l in r.json()["line_items"]] == [
        (pl[0], Decimal("194.20")), (pl[2], Decimal("33.80"))]

    # Admission side: an edit that touches neither invoices nor lines leaves them be.
    r = await admin_client.patch(f"{PA_URL}/{pa_id}", json={"title": "Renamed"})
    assert r.status_code == 200, r.text
    assert len(r.json()["line_items"]) == 2
