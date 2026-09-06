"""One payment application settling several purchase orders.

Covers the rules that make a multi-PO payment safe rather than merely possible:
the whole PO set is persisted (not just the primary), every PO's receipt gate is
checked, a mixed vendor / currency / payment type is refused, "which PAs pay
this PO" answers for a secondary PO too, and the PO's Create-PA prompt clears
for every order on the payment.
"""
import uuid as _uuid
from datetime import date as _date
from decimal import Decimal as _Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

PA_URL = "/api/v1/pa"
PO_URL = "/api/v1/po"
VENDOR_URL = "/api/v1/vendors"

_PO_LINE = {"description": "Filter Set", "qty": "5", "unit": "EA", "unit_price": "80.00"}


async def _make_vendor(client, name="Multi PO Vendor"):
    r = await client.post(VENDOR_URL, json={
        "code": f"VND-MPO-{_uuid.uuid4().hex[:8].upper()}", "name": name, "category": "Parts",
        "contact_name": "V", "contact_email": "v@v.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_po(client, vendor_id, currency="CAD"):
    r = await client.post(PO_URL, json={
        "title": "Multi-PO Test PO", "type": 2, "vendor_id": vendor_id,
        "currency": currency, "tax_rate": "0.13", "line_items": [_PO_LINE],
    })
    r.raise_for_status()
    return r.json()


async def _make_three_way_po(client, test_engine, vendor_id, currency="CAD"):
    """A PO that already satisfies the receipt gate: a GoodsReceipt plus a
    'matched' Invoice pointing at it. Mirrors tests/test_pa.py's helper."""
    from app.crud import user as user_crud
    from app.models.gr import GoodsReceipt
    from app.models.invoice import Invoice
    from app.schemas.auth import RegisterRequest

    po = await _make_po(client, vendor_id, currency=currency)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"mpo-{_uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Multi PO Tester", role="warehouse_staff",
        ))
        gr = GoodsReceipt(
            number=f"GR-{_uuid.uuid4().hex[:8]}", title="Test GR",
            po_id=_uuid.UUID(po["id"]), po_number=po["number"],
            vendor_id=_uuid.UUID(vendor_id), vendor_name="Multi PO Vendor",
            gr_type="standard", procurement_type=1, currency=currency,
            status="pending_ack", created_by=user.id,
        )
        db.add(gr)
        await db.flush()
        inv = Invoice(
            internal_ref=f"I-{_uuid.uuid4().hex[:6]}",
            vendor_invoice_number=f"I-{_uuid.uuid4().hex[:6]}",
            vendor_id=_uuid.UUID(vendor_id), vendor_name="Multi PO Vendor",
            amount=_Decimal("400"), tax_amount=_Decimal("52"), total_amount=_Decimal("452"),
            invoice_date=_date(2026, 1, 1), due_date=_date(2026, 2, 1),
            status="matched", line_items=[],
            po_id=_uuid.UUID(po["id"]), gr_id=gr.id, uploaded_by=user.id,
        )
        db.add(inv)
        await db.commit()
        return po, str(inv.id)


def _payload(po_ids, **overrides):
    base = {
        "po_ids": po_ids,
        "title": "Multi-PO Payment",
        "pa_type": "regular",
        "subtotal": "800.00",
        "tax_amount": "104.00",
        "currency": "CAD",
    }
    base.update(overrides)
    return base


# ── The set is persisted, and the primary is the first entry ───────────────────

@pytest.mark.asyncio
async def test_create_pa_covering_two_pos(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])

    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))
    assert r.status_code == 201, r.text
    pa = r.json()

    assert pa["po_ids"] == [po1["id"], po2["id"]]
    assert pa["po_numbers"] == [po1["number"], po2["number"]]
    # The header keeps the PRIMARY PO — every mirror and export still reads it.
    assert pa["po_id"] == po1["id"]
    assert pa["po_number"] == po1["number"]


@pytest.mark.asyncio
async def test_single_po_body_still_works(admin_client, test_engine):
    """The old single-po_id body is unchanged and now also produces a link row,
    so "which POs does this PA pay" has one answer for every PA."""
    v = await _make_vendor(admin_client)
    po, inv = await _make_three_way_po(admin_client, test_engine, v["id"])

    r = await admin_client.post(PA_URL, json={
        "po_id": po["id"], "title": "Single", "pa_type": "regular",
        "subtotal": "400.00", "tax_amount": "52.00", "currency": "CAD",
        "invoice_ids": [inv],
    })
    assert r.status_code == 201, r.text
    pa = r.json()
    assert pa["po_ids"] == [po["id"]]
    assert pa["po_id"] == po["id"]


# ── "Which PAs pay this PO" must answer for the SECOND PO too ──────────────────

@pytest.mark.asyncio
async def test_secondary_po_lists_the_payment(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])
    created = (await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))).json()

    for po in (po1, po2):
        r = await admin_client.get(PA_URL, params={"po_id": po["id"]})
        assert r.status_code == 200, r.text
        assert created["id"] in [p["id"] for p in r.json()["items"]], (
            f"payment missing from {po['number']}'s payment list")


@pytest.mark.asyncio
async def test_search_finds_payment_by_secondary_po_number(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])
    created = (await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))).json()

    r = await admin_client.get(PA_URL, params={"search": po2["number"]})
    assert created["id"] in [p["id"] for p in r.json()["items"]]


# ── Coherence: vendor / currency / payment type ────────────────────────────────

@pytest.mark.asyncio
async def test_mixed_vendors_refused(admin_client, test_engine):
    v1 = await _make_vendor(admin_client, "Vendor One")
    v2 = await _make_vendor(admin_client, "Vendor Two")
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v1["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v2["id"])

    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))
    assert r.status_code == 422, r.text
    assert "same vendor" in r.json()["detail"]


@pytest.mark.asyncio
async def test_mixed_currencies_refused(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"], currency="CAD")
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"], currency="USD")

    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))
    assert r.status_code == 422, r.text
    assert "same currency" in r.json()["detail"]


@pytest.mark.asyncio
async def test_multi_po_prepayment_refused(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])

    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2],
        pa_type="prepayment", prepayment_pct="50",
        expected_settlement_date="2026-12-31"))
    assert r.status_code == 422, r.text
    assert "regular" in r.json()["detail"]


# ── The receipt gate applies to EVERY PO, not just the primary ────────────────

@pytest.mark.asyncio
async def test_receipt_gate_checks_every_po(admin_client, test_engine):
    """PO 1 is fully three-way matched, PO 2 has nothing. Without a per-PO gate
    the payment would go out settling an order with no receipt at all."""
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2 = await _make_po(admin_client, v["id"])            # no GR, no invoice

    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1]))
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert po2["number"] in detail
    assert po1["number"] not in detail   # names only the PO that is actually short


@pytest.mark.asyncio
async def test_receipt_override_covers_the_ungated_po(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2 = await _make_po(admin_client, v["id"])

    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1],
        receipt_override=True, receipt_override_reason="freight in transit"))
    assert r.status_code == 201, r.text
    assert r.json()["receipt_override"] is True


# ── An invoice must belong to one of the POs on the payment ───────────────────

@pytest.mark.asyncio
async def test_invoice_from_an_unlisted_po_refused(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])

    # Pay PO 1 only, but link PO 2's invoice.
    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"]], invoice_ids=[inv2]))
    assert r.status_code == 422, r.text
    assert "do not belong" in r.json()["detail"]


@pytest.mark.asyncio
async def test_invoice_from_either_po_accepted(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])

    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))
    assert r.status_code == 201, r.text
    assert set(r.json()["invoice_ids"]) == {inv1, inv2}


# ── The Create-PA prompt clears for every PO on the payment ───────────────────

@pytest.mark.asyncio
async def test_create_pa_task_clears_for_every_po(admin_client, test_engine):
    """A PO covered as the SECOND order on a payment is paid. Its "Create Payment
    Application" prompt must close, or the requester is nagged forever to raise a
    payment that already exists."""
    from app.models.task import Task

    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        for po in (po1, po2):
            db.add(Task(
                type="create_pa", priority="normal",
                document_type="po", document_id=_uuid.UUID(po["id"]),
                document_number=po["number"], assigned_role="requester",
                title=f"Create Payment Application for {po['number']}",
                is_completed=False,
            ))
        await db.commit()

    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))
    assert r.status_code == 201, r.text

    async with factory() as db:
        for po in (po1, po2):
            open_tasks = (await db.execute(select(Task).where(
                Task.type == "create_pa",
                Task.document_id == _uuid.UUID(po["id"]),
                Task.is_completed.is_(False),
            ))).scalars().all()
            assert not open_tasks, f"{po['number']} still prompts to create a payment"


# ── Editing the PO set on a draft ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_replaces_the_po_set(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])
    pa = (await admin_client.post(PA_URL, json=_payload(
        [po1["id"]], invoice_ids=[inv1]))).json()

    r = await admin_client.patch(f"{PA_URL}/{pa['id']}", json={
        "po_ids": [po1["id"], po2["id"]], "invoice_ids": [inv1, inv2],
    })
    assert r.status_code == 200, r.text
    assert r.json()["po_ids"] == [po1["id"], po2["id"]]


@pytest.mark.asyncio
async def test_patch_refuses_an_empty_po_set(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    pa = (await admin_client.post(PA_URL, json=_payload(
        [po1["id"]], invoice_ids=[inv1]))).json()

    r = await admin_client.patch(f"{PA_URL}/{pa['id']}", json={"po_ids": []})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_patch_refuses_dropping_a_po_whose_invoice_stays_linked(admin_client, test_engine):
    """Removing a PO while its invoice is still on the payment would leave the
    payment settling an invoice for an order it no longer covers."""
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])
    pa = (await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))).json()

    r = await admin_client.patch(f"{PA_URL}/{pa['id']}", json={"po_ids": [po1["id"]]})
    assert r.status_code == 422, r.text
    assert "do not belong" in r.json()["detail"]


@pytest.mark.asyncio
async def test_patch_dropping_a_po_reopens_its_create_pa_prompt(admin_client, test_engine):
    from app.models.task import Task

    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(Task(
            type="create_pa", priority="normal",
            document_type="po", document_id=_uuid.UUID(po2["id"]),
            document_number=po2["number"], assigned_role="requester",
            title="Create Payment Application",
            is_completed=False,
        ))
        await db.commit()

    pa = (await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))).json()

    r = await admin_client.patch(f"{PA_URL}/{pa['id']}", json={
        "po_ids": [po1["id"]], "invoice_ids": [inv1],
    })
    assert r.status_code == 200, r.text

    async with factory() as db:
        reopened = (await db.execute(select(Task).where(
            Task.type == "create_pa",
            Task.document_id == _uuid.UUID(po2["id"]),
            Task.is_completed.is_(False),
        ))).scalars().all()
        assert reopened, "dropping the PO left it with a closed prompt and no payment"


# ── One payment routes through one department ─────────────────────────────────

async def _attach_pr_in_new_department(test_engine, po_id, user_email_hint):
    """Give the PO a PR that belongs to a brand-new department, and return that
    department's id. Approval routing reads exactly this (PO → PR.department_id),
    which is why the PA gate compares it."""
    import uuid as _u
    from app.crud import user as user_crud
    from app.models.department import Department
    from app.models.po import PurchaseOrder
    from app.models.pr import PurchaseRequest
    from app.schemas.auth import RegisterRequest

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"{user_email_hint}-{_u.uuid4().hex[:8]}@example.com",
            password="TestPass1!", full_name="Dept Tester", role="requester"))
        dept = Department(code=f"D{_u.uuid4().hex[:5].upper()}", name="Multi PO Dept", is_active=True)
        db.add(dept)
        await db.flush()
        pr = PurchaseRequest(number=f"PR-{_u.uuid4().hex[:8]}", title="Dept PR", type=2,
                             created_by=user.id, department_id=dept.id)
        db.add(pr)
        await db.flush()
        po = await db.get(PurchaseOrder, _u.UUID(po_id))
        po.pr_id = pr.id
        await db.commit()
        return str(dept.id)


@pytest.mark.asyncio
async def test_mixed_departments_refused(admin_client, test_engine):
    """A payment is approved by ONE department's reviewers — the primary PO's.
    Combining departments would route the other department's spend past its own
    approver, which is weaker than the two payments it replaces."""
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])
    d1 = await _attach_pr_in_new_department(test_engine, po1["id"], "dept-a")
    d2 = await _attach_pr_in_new_department(test_engine, po2["id"], "dept-b")
    assert d1 != d2

    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))
    assert r.status_code == 422, r.text
    assert "same department" in r.json()["detail"]


@pytest.mark.asyncio
async def test_same_department_accepted(admin_client, test_engine):
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    po2, inv2 = await _make_three_way_po(admin_client, test_engine, v["id"])
    dept_id = await _attach_pr_in_new_department(test_engine, po1["id"], "dept-same")

    # Point PO 2's PR at the SAME department.
    import uuid as _u
    from app.models.po import PurchaseOrder
    from app.models.pr import PurchaseRequest
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        po1_row = await db.get(PurchaseOrder, _u.UUID(po1["id"]))
        pr2 = PurchaseRequest(number=f"PR-{_u.uuid4().hex[:8]}", title="Same Dept PR", type=2,
                              created_by=(await db.get(PurchaseRequest, po1_row.pr_id)).created_by,
                              department_id=_u.UUID(dept_id))
        db.add(pr2)
        await db.flush()
        po2_row = await db.get(PurchaseOrder, _u.UUID(po2["id"]))
        po2_row.pr_id = pr2.id
        await db.commit()

    r = await admin_client.post(PA_URL, json=_payload(
        [po1["id"], po2["id"]], invoice_ids=[inv1, inv2]))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_department_gate_does_not_apply_to_a_single_po(admin_client, test_engine):
    """The gate is about combining orders; one order can always be paid."""
    v = await _make_vendor(admin_client)
    po1, inv1 = await _make_three_way_po(admin_client, test_engine, v["id"])
    await _attach_pr_in_new_department(test_engine, po1["id"], "dept-solo")

    r = await admin_client.post(PA_URL, json=_payload([po1["id"]], invoice_ids=[inv1]))
    assert r.status_code == 201, r.text
