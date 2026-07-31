"""Multi-PO line-level allocation tests."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.schemas.auth import RegisterRequest

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


async def _link_po_to_pr(test_engine, po_id: str):
    """Insert a minimal PR row and point the given (already API-created) PO's
    pr_id at it, so `_on_invoice_matched`'s `po.pr_id` lookup finds a
    requester. A PR-less PO makes that dispatch return early at the `po.pr_id`
    check, which would make a "no create_pa task" assertion pass trivially."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await user_crud.create(db, RegisterRequest(
            email=f"feeonly-req-{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass1!", full_name="Fee Only Requester", role="requester"))
        await db.commit()
        pr = PurchaseRequest(number=f"PR-FEEONLY-{uuid.uuid4().hex[:6]}", title="Fee-only test PR",
                              type=2, created_by=requester.id)
        db.add(pr)
        await db.commit()
        await db.refresh(pr)
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == uuid.UUID(po_id))
        )).scalar_one()
        po.pr_id = pr.id
        await db.commit()
        return requester.id


async def _create_pa_task_for_po(test_engine, po_id: str):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        return (await db.execute(select(Task).where(
            Task.type == "create_pa", Task.document_type == "po",
            Task.document_id == uuid.UUID(po_id),
        ))).scalar_one_or_none()


async def _confirm_receipt_task_for_po(test_engine, po_id: str):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        return (await db.execute(select(Task).where(
            Task.type == "confirm_receipt", Task.document_type == "po",
            Task.document_id == uuid.UUID(po_id), Task.is_completed.is_(False),
        ))).scalar_one_or_none()


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


@pytest.mark.asyncio
async def test_partial_invoicing_cumulative(admin_client):
    """部分开票:少开放行;累计恰好对上照常 matched;累计超开才 exception。"""
    v = await _make_vendor(admin_client, "VND-ALLOC-PART-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "5 units", "qty": "5", "unit": "EA", "unit_price": "40.00"}])
    line = po["line_items"][0]["id"]

    def payload(no, amt):
        return _inv_payload(v["id"], vendor_invoice_number=no, amount=amt, tax_amount="0.00",
            line_items=[{"description": "part", "quantity": "1",
                         "unit_price": amt, "line_total": amt}])

    async def match(inv, amt):
        return await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
            {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
             "po_line_id": line, "allocated_amount": amt, "allocated_tax": "0.00"}]})

    a = (await admin_client.post(INV_URL, json=payload("PART-A", "120.00"))).json()
    r = await match(a, "120.00")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"        # 3/5 少开 → 放行

    b = (await admin_client.post(INV_URL, json=payload("PART-B", "80.00"))).json()
    r = await match(b, "80.00")
    assert r.json()["status"] == "matched"        # 累计 200 = line 总额

    c = (await admin_client.post(INV_URL, json=payload("PART-C", "50.00"))).json()
    r = await match(c, "50.00")
    assert r.json()["status"] == "exception"      # 累计 250 超开 → 拦


@pytest.mark.asyncio
async def test_non_po_fee_line_lets_mixed_invoice_match(admin_client):
    """含 shipping 行的发票:goods 分到 PO,shipping 标记为非PO费用 →
    goods_alloc + 非PO合计 = 发票税前额 → matched;标记与备注持久化。"""
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-NONPO-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "Widget", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    po_line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], vendor_invoice_number="INV-NONPO-01",
        amount="1150.00", tax_amount="0.00",
        line_items=[
            {"description": "Widget", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"},
            {"description": "Shipping & handling", "quantity": "1", "unit_price": "150.00", "line_total": "150.00"},
        ]))).json()
    goods_line = inv["line_items"][0]["id"]
    ship_line = inv["line_items"][1]["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [
            {"invoice_line_id": goods_line, "po_id": po["id"], "po_line_id": po_line,
             "allocated_amount": "1000.00", "allocated_tax": "0.00"},
        ],
        "non_po_lines": [{"line_id": ship_line, "note": "freight"}],
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"
    assert len(data["allocations"]) == 1          # shipping 不产生 allocation
    ship = next(li for li in data["line_items"] if li["id"] == ship_line)
    goods = next(li for li in data["line_items"] if li["id"] == goods_line)
    assert ship["non_po_fee"] is True
    assert ship["non_po_note"] == "freight"
    assert goods["non_po_fee"] is False           # 未标记的行默认 False
    # 头部 variance 只应衡量 PO 匹配部分:发票 1150 - 非PO费用 150 = 1000,
    # 与 PO 参照 1000 相等 → variance 应为 0,不应被非PO费用污染(Finding 2)。
    assert float(data["variance"]) == 0.0
    assert float(data["po_total"]) == 1000.00


@pytest.mark.asyncio
async def test_non_po_line_also_allocated_rejected_422(admin_client):
    """同一行不能既分配到 PO 又标记为非PO费用(会在平账里被双重计入,
    可能掩盖真实超/少开)→ 422(Finding 1)。"""
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-NONPO-OVERLAP")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "Widget", "qty": "1", "unit": "EA", "unit_price": "1000.00"},
               {"description": "Freight line", "qty": "1", "unit": "EA", "unit_price": "150.00"}])
    po_goods = po["line_items"][0]["id"]
    po_ship = po["line_items"][1]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], vendor_invoice_number="INV-NONPO-OVERLAP",
        amount="1150.00", tax_amount="0.00",
        line_items=[
            {"description": "Widget", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"},
            {"description": "Shipping", "quantity": "1", "unit_price": "150.00", "line_total": "150.00"},
        ]))).json()
    goods_line = inv["line_items"][0]["id"]
    ship_line = inv["line_items"][1]["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [
            {"invoice_line_id": goods_line, "po_id": po["id"], "po_line_id": po_goods,
             "allocated_amount": "1000.00", "allocated_tax": "0.00"},
            {"invoice_line_id": ship_line, "po_id": po["id"], "po_line_id": po_ship,
             "allocated_amount": "150.00", "allocated_tax": "0.00"},
        ],
        "non_po_lines": [{"line_id": ship_line, "note": "freight"}],
    })
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_unmarked_fee_line_still_imbalances_422(admin_client):
    """同样的混合发票,若不标记 shipping 也不分配它 → 仍 422(证明门禁未被架空)。"""
    v = await _make_vendor(admin_client, "VND-NONPO-422")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "Widget", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    po_line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], vendor_invoice_number="INV-NONPO-422",
        amount="1150.00", tax_amount="0.00",
        line_items=[
            {"description": "Widget", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"},
            {"description": "Shipping", "quantity": "1", "unit_price": "150.00", "line_total": "150.00"},
        ]))).json()
    goods_line = inv["line_items"][0]["id"]
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": goods_line, "po_id": po["id"], "po_line_id": po_line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_non_po_fee_unmark_on_rematch_clears_flag(admin_client):
    """re-match 以入参为准:把原先标记的 shipping 改成分配到真实 PO 行且不再传
    non_po_lines → 标记被清除。用 exception 态(可 re-match)构造。"""
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-NONPO-UNMARK")
    # goods PO 行 500(小于开票 1000 → 超开触发 exception,便于随后 re-match);
    # shipping PO 行 150(第二次匹配用)
    po = await _make_issued_po(admin_client, v["id"], lines=[
        {"description": "Widget", "qty": "1", "unit": "EA", "unit_price": "500.00"},
        {"description": "Freight line", "qty": "1", "unit": "EA", "unit_price": "150.00"},
    ])
    po_goods = po["line_items"][0]["id"]
    po_ship = po["line_items"][1]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], vendor_invoice_number="INV-NONPO-UNMARK",
        amount="1150.00", tax_amount="0.00",
        line_items=[
            {"description": "Widget", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"},
            {"description": "Shipping", "quantity": "1", "unit_price": "150.00", "line_total": "150.00"},
        ]))).json()
    goods_line = inv["line_items"][0]["id"]
    ship_line = inv["line_items"][1]["id"]

    # match #1: goods 分到 500(超开 → exception),shipping 标记非PO
    r1 = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [{"invoice_line_id": goods_line, "po_id": po["id"],
                         "po_line_id": po_goods, "allocated_amount": "1000.00", "allocated_tax": "0.00"}],
        "non_po_lines": [{"line_id": ship_line, "note": "freight"}],
    })
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "exception"
    ship1 = next(li for li in r1.json()["line_items"] if li["id"] == ship_line)
    assert ship1["non_po_fee"] is True

    # match #2(exception 可 re-match):shipping 改分到真实 PO 行,不传 non_po_lines
    r2 = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": goods_line, "po_id": po["id"], "po_line_id": po_goods,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
        {"invoice_line_id": ship_line, "po_id": po["id"], "po_line_id": po_ship,
         "allocated_amount": "150.00", "allocated_tax": "0.00"},
    ]})
    assert r2.status_code == 200, r2.text
    ship2 = next(li for li in r2.json()["line_items"] if li["id"] == ship_line)
    assert ship2["non_po_fee"] is False           # unmark:标记已清
    assert ship2["non_po_note"] is None


@pytest.mark.asyncio
async def test_fee_only_invoice_links_to_po(admin_client):
    """Freight-only invoice: single line marked non-PO fee, linked to a PO by
    reference_po_id → matched, header carries the PO, no allocations, zero variance."""
    v = await _make_vendor(admin_client, "VND-FEEONLY-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "Goods", "qty": "1", "unit": "EA", "unit_price": "1915.90"}])

    inv = await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="132.52", tax_amount="17.23",
        line_items=[{"description": "FREIGHT CHARGES", "quantity": "1",
                     "unit_price": "132.52", "line_total": "132.52"}]))
    inv = inv.json()
    fee_line = inv["line_items"][0]["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [],
        "non_po_lines": [{"line_id": fee_line, "note": "freight"}],
        "reference_po_id": po["id"],
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"
    assert data["po_id"] == po["id"]
    assert data["po_number"] == po["number"]
    assert data["allocations"] == []
    assert float(data["variance"]) == 0.0
    assert float(data["po_total"]) == 0.0
    fee = next(li for li in data["line_items"] if li["id"] == fee_line)
    assert fee["non_po_fee"] is True


@pytest.mark.asyncio
async def test_fee_only_invoice_without_link_rejected(admin_client):
    """Fee-only invoice with no reference_po_id → 422, cannot confirm."""
    v = await _make_vendor(admin_client, "VND-FEEONLY-02")
    inv = await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="132.52", tax_amount="0.00",
        line_items=[{"description": "FREIGHT", "quantity": "1",
                     "unit_price": "132.52", "line_total": "132.52"}]))
    inv = inv.json()
    fee_line = inv["line_items"][0]["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [],
        "non_po_lines": [{"line_id": fee_line, "note": "freight"}],
    })
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_fee_only_invoice_imbalanced_rejected(admin_client):
    """Zero allocations but fees don't cover the full pre-tax amount → 422."""
    v = await _make_vendor(admin_client, "VND-FEEONLY-03")
    po = await _make_issued_po(admin_client, v["id"])
    inv = await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[
            {"description": "part-a", "quantity": "1", "unit_price": "600.00", "line_total": "600.00"},
            {"description": "part-b", "quantity": "1", "unit_price": "400.00", "line_total": "400.00"},
        ]))
    inv = inv.json()
    only_line = inv["line_items"][0]["id"]   # mark only 600 of 1000 as fee

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [],
        "non_po_lines": [{"line_id": only_line, "note": "partial fee"}],
        "reference_po_id": po["id"],
    })
    assert r.status_code == 422, r.text


def test_match_request_accepts_reference_po_id():
    from app.schemas.invoice import InvoiceMatchRequest
    import uuid
    req = InvoiceMatchRequest(allocations=[], reference_po_id=uuid.uuid4())
    assert req.reference_po_id is not None
    # optional by default
    assert InvoiceMatchRequest(allocations=[]).reference_po_id is None


@pytest.mark.asyncio
async def test_allocated_match_to_pr_backed_po_creates_confirm_receipt_task(admin_client, test_engine):
    """Control for the fee-only negative below: a NORMAL (allocated) match to a
    PR-backed PO DOES fire the post-match dispatch hook. This PO has no GR yet,
    so the match is not yet 3-way — the hook creates a confirm_receipt task
    (not create_pa) for the PR's requester. Proves _on_invoice_matched fires
    when it should, so the fee-only test's "no task" assertion is meaningful
    rather than a trivial no-op."""
    v = await _make_vendor(admin_client, "VND-FEEONLY-PA-01")
    po = await _make_issued_po(admin_client, v["id"])
    await _link_po_to_pr(test_engine, po["id"])

    inv = (await admin_client.post(INV_URL, json=_inv_payload(v["id"]))).json()
    line_id = inv["line_items"][0]["id"]
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [{"invoice_line_id": line_id, "po_id": po["id"],
                         "allocated_amount": "1000.00", "allocated_tax": "0.00"}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"

    task = await _confirm_receipt_task_for_po(test_engine, po["id"])
    assert task is not None


@pytest.mark.asyncio
async def test_fee_only_match_skips_create_pa_task(admin_client, test_engine):
    """A fee-only match linked to a PR-backed PO must NOT spawn a create_pa
    task: the fees are paid in full via the AP header, no PA is expected for
    this PO on account of this invoice. (Regression: previously invoice.po_id
    being set on the fee-only branch made _notify_requester_create_pa's
    `if not invoice.po_id: return` guard falsely pass, spuriously creating a
    create_pa task sized at the PO's full total.)"""
    v = await _make_vendor(admin_client, "VND-FEEONLY-PA-02")
    po = await _make_issued_po(admin_client, v["id"])
    await _link_po_to_pr(test_engine, po["id"])

    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="132.52", tax_amount="17.23",
        line_items=[{"description": "FREIGHT", "quantity": "1",
                     "unit_price": "132.52", "line_total": "132.52"}]))).json()
    fee_line = inv["line_items"][0]["id"]
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [],
        "non_po_lines": [{"line_id": fee_line, "note": "freight"}],
        "reference_po_id": po["id"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"

    task = await _create_pa_task_for_po(test_engine, po["id"])
    assert task is None


@pytest.mark.asyncio
async def test_fee_only_invoice_cross_vendor_po_rejected(admin_client):
    """reference_po_id pointing at a PO for a DIFFERENT vendor than the invoice
    → 422 (the UI only ever offers same-vendor candidates; the API must not
    silently trust an out-of-band mismatch)."""
    v_invoice = await _make_vendor(admin_client, "VND-FEEONLY-XV-01")
    v_po = await _make_vendor(admin_client, "VND-FEEONLY-XV-02")
    po = await _make_issued_po(admin_client, v_po["id"])

    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v_invoice["id"], amount="132.52", tax_amount="0.00",
        line_items=[{"description": "FREIGHT", "quantity": "1",
                     "unit_price": "132.52", "line_total": "132.52"}]))).json()
    fee_line = inv["line_items"][0]["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [],
        "non_po_lines": [{"line_id": fee_line, "note": "freight"}],
        "reference_po_id": po["id"],
    })
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_fee_only_rematch_clears_stale_exception_reason(admin_client):
    """A previously-"exception" invoice that gets re-matched as fee-only must
    not carry the old exception_reason forward — variance is definitionally 0
    on the fee-only path."""
    v = await _make_vendor(admin_client, "VND-FEEONLY-EX-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "100.00"}])
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "Overshoot", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    line_id = inv["line_items"][0]["id"]

    # match #1: allocate the full 1000 against a PO line worth only 100 →
    # massive overshoot → status "exception" with a non-null exception_reason.
    r1 = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [{"invoice_line_id": line_id, "po_id": po["id"],
                         "allocated_amount": "1000.00", "allocated_tax": "0.00"}],
    })
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "exception"
    assert r1.json()["exception_reason"]

    # match #2: re-match as fee-only, linked to the same (same-vendor) PO for
    # traceability.
    r2 = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [],
        "non_po_lines": [{"line_id": line_id, "note": "reclassified as fee"}],
        "reference_po_id": po["id"],
    })
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["status"] == "matched"
    assert data["exception_reason"] is None


import base64, json

def _jwt_sub(client) -> str:
    tok = client.headers["Authorization"].split(" ", 1)[1]
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["sub"]


async def _set_po_status(po_id, status):
    """`_make_issued_po` (name notwithstanding) leaves the PO in its
    post-creation default status ("draft"); `list_match_candidates` filters to
    `_MATCHABLE_PO_STATUSES`, so tests exercising that endpoint must push the
    PO into an issued-like status directly."""
    import app.db.session as session_module
    from sqlalchemy import update as sa_update
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(sa_update(PurchaseOrder)
                         .where(PurchaseOrder.id == uuid.UUID(po_id)).values(status=status))
        await db.commit()


async def _create_invoice_uploaded_by(admin_client, vendor_id, uploader_id, **overrides):
    """Create an invoice row owned by `uploader_id` (bypasses the upload
    permission gate — we are testing MATCH auth, not upload auth)."""
    import uuid as _uuid
    import app.db.session as session_module
    from app.crud import invoice as invoice_crud
    from app.schemas.invoice import InvoiceCreate
    payload = _inv_payload(vendor_id, **overrides)
    async with session_module.AsyncSessionLocal() as db:
        inv = await invoice_crud.create(
            db, InvoiceCreate(**{**payload, "vendor_id": _uuid.UUID(vendor_id)}),
            vendor_name="Alloc Vendor", uploaded_by=_uuid.UUID(uploader_id))
        await db.commit()
        return {"id": str(inv.id), "line_items": [{"id": str(li.get("id"))} for li in (inv.line_items or [])]}


@pytest.mark.asyncio
async def test_uploader_can_match_own_invoice(admin_client, requester_client):
    """A non-AP uploader can match their own invoice directly (no AP task)."""
    v = await _make_vendor(admin_client, "VND-SELFMATCH-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "W", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    uploader_id = _jwt_sub(requester_client)
    inv = await _create_invoice_uploaded_by(admin_client, v["id"], uploader_id,
        amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"}])
    # requester (uploader, non-AP) matches via the whole-invoice legacy path
    r = await requester_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"          # exact vs subtotal, no match_review


@pytest.mark.asyncio
async def test_non_uploader_non_ap_cannot_match(admin_client, requester_client):
    """A non-AP user who did NOT upload the invoice is still blocked (403)."""
    v = await _make_vendor(admin_client, "VND-SELFMATCH-02")
    po = await _make_issued_po(admin_client, v["id"])
    # invoices.uploaded_by has an FK to users, so the "other" uploader must be a
    # real user row — use the admin fixture's own id (not the requester's).
    other_id = _jwt_sub(admin_client)
    inv = await _create_invoice_uploaded_by(admin_client, v["id"], other_id,
        amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"}])
    r = await requester_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_uploader_can_list_match_candidates(admin_client, requester_client):
    v = await _make_vendor(admin_client, "VND-SELFMATCH-03")
    po = await _make_issued_po(admin_client, v["id"])
    await _set_po_status(po["id"], "issued")
    uploader_id = _jwt_sub(requester_client)
    inv = await _create_invoice_uploaded_by(admin_client, v["id"], uploader_id,
        amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"}])
    r = await requester_client.get(f"{INV_URL}/{inv['id']}/match-candidates")
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) >= 1


@pytest.mark.asyncio
async def test_match_candidates_report_already_allocated_total(admin_client):
    """A PO header-billed by one invoice reports already_allocated_total to the next."""
    v = await _make_vendor(admin_client, "VND-ALLOCTOT-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "W", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    await _set_po_status(po["id"], "issued")  # match-candidates filters to matchable statuses
    # invoice 1 total-matches the whole PO (header-level, legacy path)
    inv1 = (await admin_client.post(INV_URL, json=_inv_payload(v["id"], amount="600.00", tax_amount="0.00",
        line_items=[{"description": "a", "quantity": "1", "unit_price": "600.00", "line_total": "600.00"}]))).json()
    r1 = await admin_client.post(f"{INV_URL}/{inv1['id']}/match", json={"po_id": po["id"]})
    assert r1.status_code == 200, r1.text
    # invoice 2 asks for candidates → sees 600 already allocated on that PO
    inv2 = (await admin_client.post(INV_URL, json=_inv_payload(v["id"], amount="400.00", tax_amount="0.00",
        line_items=[{"description": "b", "quantity": "1", "unit_price": "400.00", "line_total": "400.00"}]))).json()
    cand = (await admin_client.get(f"{INV_URL}/{inv2['id']}/match-candidates")).json()
    the_po = next(p for p in cand["items"] if p["id"] == po["id"])
    assert float(the_po["already_allocated_total"]) == 600.0
    # and it EXCLUDES the requesting invoice's own allocations (0 here)
    cand_self = (await admin_client.get(f"{INV_URL}/{inv1['id']}/match-candidates")).json()
    po_self = next(p for p in cand_self["items"] if p["id"] == po["id"])
    assert float(po_self["already_allocated_total"] or 0) == 0.0


@pytest.mark.asyncio
async def test_total_value_multi_po_balances_matched(admin_client):
    """One invoice total-matched header-level across TWO POs (po_line_id null) balances → matched."""
    v = await _make_vendor(admin_client, "VND-TOTMULTI-01")
    po_a = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "A", "qty": "1", "unit": "EA", "unit_price": "600.00"}])
    po_b = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "B", "qty": "1", "unit": "EA", "unit_price": "400.00"}])
    inv = (await admin_client.post(INV_URL, json=_inv_payload(v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "combined", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    anchor = inv["line_items"][0]["id"]
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": anchor, "po_id": po_a["id"], "po_line_id": None, "allocated_amount": "600.00", "allocated_tax": "0.00"},
        {"invoice_line_id": anchor, "po_id": po_b["id"], "po_line_id": None, "allocated_amount": "400.00", "allocated_tax": "0.00"},
    ]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"
    assert len(data["allocations"]) == 2
    assert all(a["po_line_id"] is None for a in data["allocations"])


@pytest.mark.asyncio
async def test_total_value_one_po_two_invoices_cumulative(admin_client):
    """A PO header-billed by two invoices reconciles cumulatively → both matched, second not over-tolerance."""
    v = await _make_vendor(admin_client, "VND-TOTCUML-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "W", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv1 = (await admin_client.post(INV_URL, json=_inv_payload(v["id"], amount="600.00", tax_amount="0.00",
        line_items=[{"description": "a", "quantity": "1", "unit_price": "600.00", "line_total": "600.00"}]))).json()
    r1 = await admin_client.post(f"{INV_URL}/{inv1['id']}/match", json={"allocations": [
        {"invoice_line_id": inv1["line_items"][0]["id"], "po_id": po["id"], "po_line_id": None,
         "allocated_amount": "600.00", "allocated_tax": "0.00"}]})
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "matched"          # partial (600<1000) still matched
    inv2 = (await admin_client.post(INV_URL, json=_inv_payload(v["id"], amount="400.00", tax_amount="0.00",
        line_items=[{"description": "b", "quantity": "1", "unit_price": "400.00", "line_total": "400.00"}]))).json()
    r2 = await admin_client.post(f"{INV_URL}/{inv2['id']}/match", json={"allocations": [
        {"invoice_line_id": inv2["line_items"][0]["id"], "po_id": po["id"], "po_line_id": None,
         "allocated_amount": "400.00", "allocated_tax": "0.00"}]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "matched"          # cumulative 600+400 == 1000, not over-tolerance


@pytest.mark.asyncio
async def test_self_match_over_tolerance_goes_to_exception(admin_client, requester_client):
    """Uploader self-match with an over-tolerance variance → exception, never match_review."""
    v = await _make_vendor(admin_client, "VND-SELFEXC-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "W", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    uploader_id = _jwt_sub(requester_client)
    inv = await _create_invoice_uploaded_by(admin_client, v["id"], uploader_id,
        amount="2000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1", "unit_price": "2000.00", "line_total": "2000.00"}])
    r = await requester_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "exception"          # 2000 vs 1000 subtotal → +100% > tolerance
    assert r.json()["status"] != "match_review"
