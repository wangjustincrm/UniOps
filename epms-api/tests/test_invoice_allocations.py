"""Multi-PO line-level allocation tests."""
import uuid

import pytest

INV_URL = "/api/v1/invoices"
VENDOR_URL = "/api/v1/vendors"
PO_URL = "/api/v1/po"

_PO_LINE = {"description": "Widget", "qty": "10", "unit": "EA", "unit_price": "100.00"}


async def _make_vendor(client, code):
    r = await client.post(VENDOR_URL, json={
        "code": code, "name": "Alloc Vendor", "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_issued_po(client, vendor_id, lines=None):
    po = await client.post(PO_URL, json={
        "title": "Alloc Test PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "line_items": lines or [_PO_LINE],
    })
    po.raise_for_status()
    # The POST response already carries line_items (each with id + line_total).
    # We do NOT GET the PO here: GET is gated by the view_po permission, which is
    # unavailable in this test env (no CompanyConfig row → all perms default False).
    # match() loads the PO by id directly and does not check PO status/visibility.
    return po.json()


async def _ensure_company_config():
    """Seed the singleton CompanyConfig so GET endpoints' view_invoice perm is on
    (defaults grant system_admin all view perms). Idempotent."""
    import app.db.session as session_module
    from app.crud import config as config_crud
    async with session_module.AsyncSessionLocal() as db:
        await config_crud.get_or_create(db)
        await db.commit()


def _inv_payload(vendor_id, **overrides):
    base = {
        "vendor_id": vendor_id, "vendor_invoice_number": "INV-ALLOC-001",
        "amount": "1000.00", "tax_amount": "130.00", "currency": "CAD",
        "invoice_date": "2026-03-20", "due_date": "2026-04-19",
        "line_items": [{"description": "Detail A", "quantity": "1",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_invoice_line_items_get_stable_id(admin_client):
    v = await _make_vendor(admin_client, "VND-ALLOC-LINEID-01")
    r = await admin_client.post(INV_URL, json=_inv_payload(v["id"]))
    assert r.status_code == 201, r.text
    lines = r.json()["line_items"]
    assert len(lines) == 1
    assert lines[0]["id"]  # backend assigned a uuid


@pytest.mark.asyncio
async def test_allocation_model_importable_and_mapped():
    from app.models.invoice_allocation import InvoicePoAllocation
    cols = {c.name for c in InvoicePoAllocation.__table__.columns}
    assert {"invoice_id", "invoice_line_id", "po_id", "po_line_id",
            "allocated_amount", "allocated_tax", "allocated_total",
            "variance", "variance_pct", "note"} <= cols
    assert InvoicePoAllocation.__tablename__ == "invoice_po_allocations"


def test_allocation_schemas_exist():
    from app.schemas.invoice import AllocationInput, AllocationResponse, InvoiceMatchRequest
    # InvoiceMatchRequest accepts an allocations list (new path)
    req = InvoiceMatchRequest(allocations=[])
    assert req.allocations == []
    # legacy single-PO fields remain optional
    import uuid
    legacy = InvoiceMatchRequest(po_id=uuid.uuid4())
    assert legacy.allocations is None


@pytest.mark.asyncio
async def test_match_two_pos_line_level_matched(admin_client):
    """发票分摊到两个 PO 各一行,合计=发票总额,各行零差 → matched。"""
    v = await _make_vendor(admin_client, "VND-ALLOC-2PO-01")
    po_a = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "A", "qty": "1", "unit": "EA", "unit_price": "600.00"}])
    po_b = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "B", "qty": "1", "unit": "EA", "unit_price": "400.00"}])
    line_a = po_a["line_items"][0]["id"]
    line_b = po_b["line_items"][0]["id"]

    inv = await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "combined", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))
    inv = inv.json()
    inv_line = inv["line_items"][0]["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po_a["id"], "po_line_id": line_a,
         "allocated_amount": "600.00", "allocated_tax": "0.00"},
        {"invoice_line_id": inv_line, "po_id": po_b["id"], "po_line_id": line_b,
         "allocated_amount": "400.00", "allocated_tax": "0.00"},
    ]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"
    assert len(data["allocations"]) == 2
    assert data["po_id"] in (po_a["id"], po_b["id"])


@pytest.mark.asyncio
async def test_legacy_match_rejects_multiple_po_line_ids(admin_client):
    """旧签名 po_line_ids 多于 1 个曾被静默丢弃(只取第一个做 reference)→ 现在必须 422,
    要求调用方改用行级 allocations。单个/零个 po_line_id 的旧路径保持兼容。"""
    v = await _make_vendor(admin_client, "VND-ALLOC-LEGACY-01")
    po = await _make_issued_po(admin_client, v["id"], lines=[
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "600.00"},
        {"description": "B", "qty": "1", "unit": "EA", "unit_price": "400.00"},
    ])
    po_line_ids = [line["id"] for line in po["line_items"]]
    assert len(po_line_ids) == 2

    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00"))).json()

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "po_id": po["id"], "po_line_ids": po_line_ids,
    })
    assert r.status_code == 422, r.text
    assert "allocations" in r.json()["detail"]


@pytest.mark.asyncio
async def test_match_allocations_must_sum_to_total(admin_client):
    v = await _make_vendor(admin_client, "VND-ALLOC-SUM-01")
    po = await _make_issued_po(admin_client, v["id"])
    line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "600.00", "allocated_tax": "0.00"},
    ]})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_match_line_over_variance_exception(admin_client):
    v = await _make_vendor(admin_client, "VND-ALLOC-VAR-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "500.00"}])
    line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "exception"
    assert float(data["allocations"][0]["variance"]) == 500.0


@pytest.mark.asyncio
async def test_get_invoice_returns_allocations(admin_client):
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-ALLOC-GET-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    r = await admin_client.get(f"{INV_URL}/{inv['id']}")
    assert r.status_code == 200, r.text
    allocs = r.json()["allocations"]
    assert len(allocs) == 1
    assert allocs[0]["po_id"] == po["id"]
    # enriched display fields resolved at read time
    assert allocs[0]["po_number"] == po["number"]
    assert allocs[0]["po_line_description"]


@pytest.mark.asyncio
async def test_visibility_via_allocation_po(admin_client):
    """Filtering invoices by a NON-primary allocated PO still finds the invoice."""
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-ALLOC-VIS-01")
    po_a = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "A", "qty": "1", "unit": "EA", "unit_price": "600.00"}])
    po_b = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "B", "qty": "1", "unit": "EA", "unit_price": "400.00"}])
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po_a["id"], "po_line_id": po_a["line_items"][0]["id"],
         "allocated_amount": "600.00", "allocated_tax": "0.00"},
        {"invoice_line_id": inv_line, "po_id": po_b["id"], "po_line_id": po_b["line_items"][0]["id"],
         "allocated_amount": "400.00", "allocated_tax": "0.00"},
    ]})
    # primary po_id is po_a (first allocation); filter by po_b → still found
    r = await admin_client.get(f"{INV_URL}?po_id={po_b['id']}")
    assert r.status_code == 200, r.text
    ids = [i["id"] for i in r.json()["items"]]
    assert inv["id"] in ids


@pytest.mark.asyncio
async def test_edit_amount_unbalances_resets_to_unmatched(admin_client):
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-ALLOC-EDIT-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    # change pre-tax amount → allocations (1000) no longer equal new total (800)
    r = await admin_client.patch(f"{INV_URL}/{inv['id']}", json={"amount": "800.00"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "unmatched"
    assert data["allocations"] == []


@pytest.mark.asyncio
async def test_match_taxed_invoice_allocates_pretax(admin_client):
    """Allocations are PRE-TAX (invoice/PO lines are pre-tax); tax stays at the
    header. A fully line-allocated taxed invoice matches with zero variance."""
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-ALLOC-TAX-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    line = po["line_items"][0]["id"]
    # pre-tax 1000 + tax 130 = total 1130; single pre-tax invoice line of 1000
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="130.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    # allocate the pre-tax line amount only (allocated_tax = 0)
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"
    assert float(data["allocations"][0]["variance"]) == 0.0


@pytest.mark.asyncio
async def test_edit_taxed_matched_invoice_keeps_match(admin_client):
    """Editing a taxed, matched invoice (e.g. to select GRs) must NOT reset it to
    unmatched: pre-tax allocations still balance against the pre-tax amount."""
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-ALLOC-EDITTAX-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="130.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    # edit without changing amount (mirrors selecting a GR) → must stay matched
    r = await admin_client.patch(f"{INV_URL}/{inv['id']}", json={"notes": "matched GR later"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"
    assert data["po_id"] == po["id"]
    assert len(data["allocations"]) == 1


@pytest.mark.asyncio
async def test_edit_gr_ids_persist_through_rematch(admin_client):
    """Selecting GRs on a matched invoice must persist (gr_ids flow through
    update→rematch→match); previously update() dropped payload.gr_ids entirely."""
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-ALLOC-EDITGR-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    gr_id = str(uuid.uuid4())
    r = await admin_client.patch(f"{INV_URL}/{inv['id']}", json={"gr_ids": [gr_id]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"
    assert data["gr_ids"] == [gr_id]   # selection persisted (not dropped)
