"""POST /invoices/{id}/unmatch-po and /unmatch-gr.

AP and Admin reversing a match. Unmatching is not "clear two columns": one
match writes the invoice header, invoice_po_allocations rows, the GR triple,
the non-PO fee flags on line_items, a create_pa/confirm_receipt task on EVERY
PO the invoice pays for, and the finance-api AP mirror. Each of those has to
come back, and the task half is the dangerous one — those tasks hang off the
PO, not the invoice, so another invoice may still be entitled to them.

Whether a PO still deserves its create_pa task is decided by
crud.po.po_has_three_way_matched_invoice, deliberately: that same function is
the PA receipt gate. Withdrawing a task while the gate would still admit the
PA would leave a payable PO with nobody told to raise the PA — the exact
failure mode this suite pins down.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.admin_audit_log import AdminAuditLog
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.schemas.auth import RegisterRequest

INV_URL = "/api/v1/invoices"
VENDOR_URL = "/api/v1/vendors"
PO_URL = "/api/v1/po"


def _sf(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _make_vendor(client):
    r = await client.post(VENDOR_URL, json={
        "code": f"VND-UNM-{uuid.uuid4().hex[:8]}", "name": "Unmatch Vendor",
        "category": "Parts", "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_po(client, vendor_id, unit_price="1000.00"):
    r = await client.post(PO_URL, json={
        "title": "Unmatch Test PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.00",
        "line_items": [{"description": "Widget", "qty": "1", "unit": "EA",
                        "unit_price": unit_price}],
    })
    r.raise_for_status()
    return r.json()


async def _link_po_to_pr(test_engine, po_id):
    """Give the PO a PR, so _on_invoice_matched actually dispatches a create_pa
    task. Without it the dispatch returns early and a "task withdrawn"
    assertion would pass for the wrong reason."""
    async with _sf(test_engine)() as db:
        requester = await user_crud.create(db, RegisterRequest(
            email=f"unm-req-{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass1!", full_name="Unmatch Requester", role="requester"))
        await db.commit()
        pr = PurchaseRequest(number=f"PR-UNM-{uuid.uuid4().hex[:6]}",
                             title="Unmatch test PR", type=2, created_by=requester.id)
        db.add(pr)
        await db.commit()
        await db.refresh(pr)
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == uuid.UUID(po_id)))).scalar_one()
        po.pr_id = pr.id
        await db.commit()


async def _make_gr(test_engine, po_id):
    async with _sf(test_engine)() as db:
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == uuid.UUID(po_id)))).scalar_one()
        gr = GoodsReceipt(
            id=uuid.uuid4(), number=f"GR-UNM-{uuid.uuid4().hex[:8]}", title="Unmatch GR",
            po_id=po.id, po_number=po.number, vendor_id=po.vendor_id,
            vendor_name=po.vendor_name, gr_type="physical", procurement_type=2,
            currency="CAD", status="confirmed", created_by=po.created_by,
        )
        db.add(gr)
        await db.commit()
        return str(gr.id)


async def _make_invoice(client, vendor_id, amount="1000.00"):
    r = await client.post(INV_URL, json={
        "vendor_id": vendor_id, "vendor_invoice_number": f"VI-{uuid.uuid4().hex[:8]}",
        "amount": amount, "tax_amount": "0.00", "currency": "CAD",
        "invoice_date": "2026-03-20", "due_date": "2026-04-19",
        "line_items": [{"description": "Widget", "quantity": "1",
                        "unit_price": amount, "line_total": amount}],
    })
    assert r.status_code == 201, r.text
    return r.json()


async def _match(client, inv, po, gr_id=None, amount="1000.00"):
    body = {"allocations": [{
        "invoice_line_id": inv["line_items"][0]["id"],
        "po_id": po["id"], "po_line_id": po["line_items"][0]["id"],
        "allocated_amount": amount, "allocated_tax": "0.00",
    }]}
    if gr_id:
        body["gr_ids"] = [gr_id]
    r = await client.post(f"{INV_URL}/{inv['id']}/match", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _matched_invoice(client, test_engine, with_gr=True, with_pr=True):
    v = await _make_vendor(client)
    po = await _make_po(client, v["id"])
    if with_pr:
        await _link_po_to_pr(test_engine, po["id"])
    gr_id = await _make_gr(test_engine, po["id"]) if with_gr else None
    inv = await _make_invoice(client, v["id"])
    matched = await _match(client, inv, po, gr_id)
    return v, po, matched, gr_id


async def _open_task(test_engine, po_id, task_type):
    async with _sf(test_engine)() as db:
        return (await db.execute(select(Task).where(
            Task.type == task_type, Task.document_type == "po",
            Task.document_id == uuid.UUID(po_id), Task.is_completed.is_(False),
        ))).scalars().first()


async def _set_status(test_engine, invoice_id, status):
    async with _sf(test_engine)() as db:
        inv = (await db.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(invoice_id)))).scalar_one()
        inv.status = status
        await db.commit()


async def _make_pa(test_engine, invoice_id, po, status="in_review"):
    async with _sf(test_engine)() as db:
        po_row = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == uuid.UUID(po["id"])))).scalar_one()
        pa = PaymentApplication(
            id=uuid.uuid4(), pa_number=f"PA-UNM-{uuid.uuid4().hex[:8]}", title="Unmatch PA",
            po_id=po_row.id, po_number=po_row.number, vendor_id=po_row.vendor_id,
            vendor_name=po_row.vendor_name, invoice_ids=[invoice_id], gr_ids=[],
            subtotal=Decimal("1000.00"), payment_amount=Decimal("1000.00"),
            tax_amount=Decimal("0.00"), currency="CAD", status=status,
            created_by=po_row.created_by,
        )
        db.add(pa)
        await db.commit()
        return pa.pa_number


# ── The two reversals ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unmatch_po_returns_the_invoice_to_unmatched(admin_client, test_engine):
    _, po, inv, _ = await _matched_invoice(admin_client, test_engine)
    assert inv["status"] == "matched" and inv["po_id"] == po["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-po",
                                json={"reason": "Wrong PO picked"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "unmatched"
    assert out["po_id"] is None and out["po_number"] is None
    # The GR belongs to the PO, so it cannot survive the PO going away.
    assert out["gr_id"] is None and not out["gr_ids"]
    assert out["matched_at"] is None

    async with _sf(test_engine)() as db:
        rows = (await db.execute(select(InvoicePoAllocation).where(
            InvoicePoAllocation.invoice_id == uuid.UUID(inv["id"])))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_unmatch_gr_clears_the_receipt_and_keeps_the_po(admin_client, test_engine):
    _, po, inv, _ = await _matched_invoice(admin_client, test_engine)

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-gr",
                                json={"reason": "GR was for a different delivery"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["gr_id"] is None and out["gr_number"] is None and not out["gr_ids"]
    # Still matched to its PO — only the receipt evidence was withdrawn.
    assert out["po_id"] == po["id"]
    assert out["status"] == "matched"

    async with _sf(test_engine)() as db:
        rows = (await db.execute(select(InvoicePoAllocation).where(
            InvoicePoAllocation.invoice_id == uuid.UUID(inv["id"])))).scalars().all()
    assert len(rows) == 1


# ── Gates ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unmatch_is_refused_while_an_active_pa_references_the_invoice(
    admin_client, test_engine
):
    _, po, inv, _ = await _matched_invoice(admin_client, test_engine)
    pa_number = await _make_pa(test_engine, inv["id"], po, status="in_review")

    for route in ("unmatch-po", "unmatch-gr"):
        r = await admin_client.post(f"{INV_URL}/{inv['id']}/{route}",
                                    json={"reason": "changed my mind"})
        assert r.status_code == 422, (route, r.text)
        # Naming the blocker is the point: "cancel the payment application
        # first" is only actionable if you know which one.
        assert pa_number in r.json()["detail"]
        assert "in_review" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_cancelled_pa_does_not_block_unmatch(admin_client, test_engine):
    _, po, inv, _ = await _matched_invoice(admin_client, test_engine)
    await _make_pa(test_engine, inv["id"], po, status="cancelled")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-po",
                                json={"reason": "PA was cancelled, redo the match"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["approved", "paid"])
async def test_unmatch_refused_once_the_invoice_has_entered_payment(
    admin_client, test_engine, status
):
    _, _, inv, _ = await _matched_invoice(admin_client, test_engine)
    await _set_status(test_engine, inv["id"], status)

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-po",
                                json={"reason": "too late"})
    assert r.status_code == 422, r.text
    assert status in r.json()["detail"]


@pytest.mark.asyncio
async def test_unmatch_requires_a_reason(admin_client, test_engine):
    _, _, inv, _ = await _matched_invoice(admin_client, test_engine)

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-po", json={"reason": "   "})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_unmatch_po_refused_on_the_agreement_route(admin_client, test_engine):
    """Agreement invoices settle against receipts/schedule rows, which unwind
    differently; this endpoint would silently leave that evidence claimed."""
    from app.models.agreement import PurchaseAgreement

    v = await _make_vendor(admin_client)
    inv = await _make_invoice(admin_client, v["id"])
    async with _sf(test_engine)() as db:
        row = (await db.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(inv["id"])))).scalar_one()
        agr = PurchaseAgreement(
            id=uuid.uuid4(), number=f"AGR-UNM-{uuid.uuid4().hex[:6]}", title="House account",
            agreement_type="house_account", vendor_id=row.vendor_id,
            vendor_name=row.vendor_name, valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31), created_by=row.uploaded_by,
        )
        db.add(agr)
        await db.flush()
        row.agreement_id = agr.id
        row.agreement_number = agr.number
        row.agreement_type = "house_account"
        row.match_route = "agreement"
        row.status = "matched"
        await db.commit()

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-po",
                                json={"reason": "wrong agreement"})
    assert r.status_code == 422, r.text
    assert "agreement" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unmatch_forbidden_without_the_match_permission(
    admin_client, requester_client, test_engine
):
    _, _, inv, _ = await _matched_invoice(admin_client, test_engine)

    r = await requester_client.post(f"{INV_URL}/{inv['id']}/unmatch-po",
                                    json={"reason": "not mine to undo"})
    assert r.status_code == 403, r.text


# ── Task rollback ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unmatch_po_withdraws_the_create_pa_task_nothing_else_backs(
    admin_client, test_engine
):
    _, po, inv, _ = await _matched_invoice(admin_client, test_engine)
    assert await _open_task(test_engine, po["id"], "create_pa") is not None, \
        "precondition: matching raised a create_pa task"

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-po",
                                json={"reason": "matched in error"})
    assert r.status_code == 200, r.text

    assert await _open_task(test_engine, po["id"], "create_pa") is None, \
        "the only invoice backing this PO is gone — nobody should still be asked to raise a PA"


@pytest.mark.asyncio
async def test_unmatch_po_keeps_the_create_pa_task_when_another_invoice_still_backs_the_po(
    admin_client, test_engine
):
    """The tasks hang off the PO, not the invoice. Closing them because ONE
    invoice was unmatched is how a payable PO ends up with no open task and
    nobody able to raise its PA."""
    v = await _make_vendor(admin_client)
    po = await _make_po(admin_client, v["id"], unit_price="2000.00")
    await _link_po_to_pr(test_engine, po["id"])
    gr_id = await _make_gr(test_engine, po["id"])

    first = await _make_invoice(admin_client, v["id"])
    await _match(admin_client, first, po, gr_id)
    second = await _make_invoice(admin_client, v["id"])
    await _match(admin_client, second, po, gr_id)

    assert await _open_task(test_engine, po["id"], "create_pa") is not None

    r = await admin_client.post(f"{INV_URL}/{first['id']}/unmatch-po",
                                json={"reason": "first invoice was a duplicate"})
    assert r.status_code == 200, r.text

    assert await _open_task(test_engine, po["id"], "create_pa") is not None, \
        "the second invoice still 3-way matches this PO — its create_pa task must survive"


@pytest.mark.asyncio
async def test_unmatch_gr_follows_the_pa_receipt_gate_not_its_own_rule(
    admin_client, test_engine
):
    """Dropping the invoice's GR link does NOT by itself make the PO
    un-payable: po_has_three_way_matched_invoice (which IS the PA receipt
    gate) still admits an allocated PO that has a GR of its own. Withdrawing
    the task here would leave a PO the gate still lets you raise a PA
    against, with nobody told to do it."""
    _, po, inv, _ = await _matched_invoice(admin_client, test_engine)
    assert await _open_task(test_engine, po["id"], "create_pa") is not None

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-gr",
                                json={"reason": "receipt attached to the wrong invoice"})
    assert r.status_code == 200, r.text

    assert await _open_task(test_engine, po["id"], "create_pa") is not None


# ── Audit ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unmatch_writes_an_audit_row_naming_what_was_undone(
    admin_client, test_engine
):
    _, po, inv, gr_id = await _matched_invoice(admin_client, test_engine)

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-po",
                                json={"reason": "Vendor sent a corrected invoice"})
    assert r.status_code == 200, r.text

    async with _sf(test_engine)() as db:
        row = (await db.execute(select(AdminAuditLog).where(
            AdminAuditLog.record_id == uuid.UUID(inv["id"]),
            AdminAuditLog.action == "unmatch",
        ))).scalars().first()
    assert row is not None, "unmatching is a financial action and must be traceable"
    assert row.entity == "invoice"
    assert row.record_number == inv["internal_ref"]
    assert row.before["po_number"] == po["number"]
    assert gr_id in (row.before.get("gr_ids") or [])
    assert row.after["reason"] == "Vendor sent a corrected invoice"
