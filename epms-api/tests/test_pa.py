"""Payment Application endpoint tests."""
import pytest

PA_URL = "/api/v1/pa"
PO_URL = "/api/v1/po"
VENDOR_URL = "/api/v1/vendors"
INV_URL = "/api/v1/invoices"

_PO_LINE = {"description": "Filter Set", "qty": "5", "unit": "EA", "unit_price": "80.00"}


async def _make_vendor(client, code):
    r = await client.post(VENDOR_URL, json={
        "code": code, "name": "PA Vendor", "category": "Parts",
        "contact_name": "V", "contact_email": "v@v.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_po(client, vendor_id):
    """Create a PO without driving it through approval (PA creation only needs
    the PO to exist; PO /action delegates to approval-api, unavailable in unit DB)."""
    po = await client.post(PO_URL, json={
        "title": "PA Test PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "line_items": [_PO_LINE],
    })
    po.raise_for_status()
    return po.json()


# _make_bare_po = _make_po: creates a PO with no invoice at all → no 3-way match.
_make_bare_po = _make_po


async def _make_three_way_po(admin_client, test_engine, vendor_id):
    """Build a PO that already satisfies the 3-way match gate: a real GoodsReceipt
    row plus a 'matched' Invoice pointing at it (Invoice.gr_id is a FK to
    goods_receipts.id). PO is created via the API (draft); the GR + matched
    invoice are inserted directly on a committed session so the HTTP endpoint's
    own session (a different connection) can see them."""
    import uuid as _uuid
    from datetime import date as _date
    from decimal import Decimal as _Decimal
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.crud import user as user_crud
    from app.models.gr import GoodsReceipt
    from app.models.invoice import Invoice
    from app.schemas.auth import RegisterRequest

    po = await _make_po(admin_client, vendor_id)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"tw-pa-{_uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="TW PA Tester", role="warehouse_staff",
        ))
        gr = GoodsReceipt(
            number=f"GR-{_uuid.uuid4().hex[:8]}", title="Test GR",
            po_id=_uuid.UUID(po["id"]), po_number=po["number"],
            vendor_id=_uuid.UUID(vendor_id), vendor_name="PA Vendor",
            gr_type="standard", procurement_type=1, currency="CAD",
            status="pending_ack", created_by=user.id,
        )
        db.add(gr)
        await db.flush()
        inv = Invoice(
            internal_ref=f"I-{_uuid.uuid4().hex[:6]}", vendor_invoice_number=f"I-{_uuid.uuid4().hex[:6]}",
            vendor_id=_uuid.UUID(vendor_id), vendor_name="PA Vendor",
            amount=_Decimal("400"), tax_amount=_Decimal("52"), total_amount=_Decimal("452"),
            invoice_date=_date(2026, 1, 1), due_date=_date(2026, 2, 1),
            status="matched", line_items=[],
            po_id=_uuid.UUID(po["id"]), gr_id=gr.id, uploaded_by=user.id,
        )
        db.add(inv)
        await db.commit()
    return po


async def _make_approved_po(client, vendor_id):
    po = await client.post(PO_URL, json={
        "title": "PA Test PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "line_items": [_PO_LINE],
    })
    po.raise_for_status()
    po_id = po.json()["id"]
    for act in ["submit", "approve", "approve"]:
        (await client.post(f"{PO_URL}/{po_id}/action", json={"action": act})).raise_for_status()
    return po.json()


def _pa_payload(po_id, **overrides):
    base = {
        "po_id": po_id,
        "title": "Filter Set Payment",
        "pa_type": "regular",
        "subtotal": "400.00",
        "tax_amount": "52.00",
        "currency": "CAD",
        "line_items": [{
            "description": "Filter Set", "qty": "5", "unit": "EA", "unit_price": "80.00"
        }],
    }
    base.update(overrides)
    return base


async def _create_pa(client, po_id, **overrides):
    r = await client.post(PA_URL, json=_pa_payload(po_id, **overrides))
    assert r.status_code == 201, r.text
    return r.json()


# ── Basic CRUD ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_pa(admin_client):
    v = await _make_vendor(admin_client, "VND-PA-CREATE-01")
    po = await _make_approved_po(admin_client, v["id"])
    pa = await _create_pa(admin_client, po["id"])
    assert pa["pa_number"].startswith("PA-")
    assert pa["status"] == "draft"
    assert float(pa["payment_amount"]) == 452.00
    assert len(pa["line_items"]) == 1


@pytest.mark.asyncio
async def test_create_pa_with_negative_discount_line(admin_client):
    """PA 行由前端直传 PO 行(含负单价折扣行);头部 subtotal 仍须非负。"""
    v = await _make_vendor(admin_client, "VND-PA-NEG-01")
    po = await _make_po(admin_client, v["id"])
    pa = await _create_pa(admin_client, po["id"], line_items=[
        {"description": "Filter Set", "qty": "5", "unit": "EA", "unit_price": "80.00"},
        {"description": "30% Discount", "qty": "1", "unit": "EA", "unit_price": "-100.00"},
    ], subtotal="300.00", tax_amount="39.00",
        receipt_override=True, receipt_override_reason="negative line test setup")
    assert float(pa["line_items"][1]["unit_price"]) == -100.00


@pytest.mark.asyncio
async def test_list_pas(admin_client):
    v = await _make_vendor(admin_client, "VND-PA-LIST-01")
    po = await _make_approved_po(admin_client, v["id"])
    await _create_pa(admin_client, po["id"])
    r = await admin_client.get(PA_URL)
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_list_pas_search(admin_client, test_engine):
    """`search` matches PA #, vendor name and PO # (what the UI advertises)."""
    # The list route gates on view_pa via the ACM; without a CompanyConfig row
    # every permission resolves False and the list is empty for everyone.
    from sqlalchemy import select as _select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models.config import CompanyConfig
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        if (await db.execute(_select(CompanyConfig).limit(1))).scalar_one_or_none() is None:
            db.add(CompanyConfig(role_permissions={}, custom_roles=[]))
            await db.commit()

    v1 = await admin_client.post(VENDOR_URL, json={
        "code": "VND-PA-SRCH-01", "name": "Srchbl Dairy Co", "category": "Parts",
        "contact_name": "V", "contact_email": "v@v.com",
        "payment_terms": "net30", "currency": "CAD"})
    v1.raise_for_status()
    v2 = await admin_client.post(VENDOR_URL, json={
        "code": "VND-PA-SRCH-02", "name": "Unrelated Supplier", "category": "Parts",
        "contact_name": "V", "contact_email": "v@v.com",
        "payment_terms": "net30", "currency": "CAD"})
    v2.raise_for_status()
    po1 = await _make_po(admin_client, v1.json()["id"])
    po2 = await _make_po(admin_client, v2.json()["id"])
    # Bare POs have no 3-way matched invoice; override the receipt gate — this
    # test exercises search, not the gate itself.
    pa1 = await _create_pa(admin_client, po1["id"],
                           receipt_override=True, receipt_override_reason="search test setup")
    pa2 = await _create_pa(admin_client, po2["id"],
                           receipt_override=True, receipt_override_reason="search test setup")

    async def _numbers(term):
        r = await admin_client.get(PA_URL, params={"search": term})
        assert r.status_code == 200, r.text
        return [p["pa_number"] for p in r.json()["items"]]

    # exact PA number
    assert await _numbers(pa1["pa_number"]) == [pa1["pa_number"]]
    # vendor name fragment, case-insensitive
    got = await _numbers("srchbl dairy")
    assert pa1["pa_number"] in got and pa2["pa_number"] not in got
    # PO number
    got = await _numbers(po1["number"])
    assert pa1["pa_number"] in got and pa2["pa_number"] not in got
    # no match
    assert await _numbers("zzz-no-such-thing") == []


@pytest.mark.asyncio
async def test_get_pa(admin_client):
    v = await _make_vendor(admin_client, "VND-PA-GET-01")
    po = await _make_approved_po(admin_client, v["id"])
    pa = await _create_pa(admin_client, po["id"])
    r = await admin_client.get(f"{PA_URL}/{pa['id']}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_list_excludes_oa_direct_pas(admin_client, test_engine):
    """Regression: OA writes PO-less Direct PAs (NULL po_id) into the shared
    payment_applications table. EPMS owns PO-based PAs only — the list must not
    500 on PaResponse validation and must exclude Direct PAs; fetching one 404s."""
    import uuid as _uuid
    from decimal import Decimal
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.crud import user as user_crud
    from app.models.pa import PaymentApplication
    from app.schemas.auth import RegisterRequest

    v = await _make_vendor(admin_client, "VND-PA-DIR-01")
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        u = await user_crud.create(db, RegisterRequest(
            email=f"oa-{_uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="OA User", role="requester"))
        direct = PaymentApplication(
            pa_number=f"PA-DIR-{_uuid.uuid4().hex[:6]}", title="OA Direct PA",
            po_id=None, po_number=None, vendor_id=_uuid.UUID(v["id"]),
            vendor_name=v["name"], subtotal=Decimal("100.00"),
            payment_amount=Decimal("100.00"), currency="CAD", status="approved",
            created_by=u.id)
        db.add(direct)
        await db.commit()
        direct_id = direct.id

    r = await admin_client.get(PA_URL)
    assert r.status_code == 200, r.text            # no 500 on the Direct PA row
    numbers = [p["pa_number"] for p in r.json()["items"]]
    assert not any(n.startswith("PA-DIR-") for n in numbers)   # excluded

    r = await admin_client.get(f"{PA_URL}/{direct_id}")
    assert r.status_code == 404                     # not an EPMS PA


@pytest.mark.asyncio
async def test_update_pa(admin_client):
    v = await _make_vendor(admin_client, "VND-PA-UPD-01")
    po = await _make_approved_po(admin_client, v["id"])
    pa = await _create_pa(admin_client, po["id"], title="Old Title")
    r = await admin_client.patch(f"{PA_URL}/{pa['id']}", json={"title": "New Title"})
    assert r.status_code == 200
    assert r.json()["title"] == "New Title"


@pytest.mark.asyncio
async def test_create_pa_invalid_po(admin_client):
    r = await admin_client.post(PA_URL, json=_pa_payload("00000000-0000-0000-0000-000000000000"))
    assert r.status_code == 404


# ── Workflow ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_pa_approval(admin_client):
    v = await _make_vendor(admin_client, "VND-PA-APPR-01")
    po = await _make_approved_po(admin_client, v["id"])
    pa = await _create_pa(admin_client, po["id"])
    pid = pa["id"]

    # submit
    r = await admin_client.post(f"{PA_URL}/{pid}/action", json={"action": "submit"})
    assert r.json()["status"] == "submitted"
    assert r.json()["submitted_at"] is not None

    # step 0 (finance_bp) → in_review
    r = await admin_client.post(f"{PA_URL}/{pid}/action", json={"action": "approve"})
    assert r.json()["status"] == "in_review"
    assert r.json()["approval_step_idx"] == 1

    # step 1 (finance_manager) → approved
    r = await admin_client.post(f"{PA_URL}/{pid}/action", json={"action": "approve"})
    assert r.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_process_pa_marks_invoices_paid(admin_client):
    """After PA is processed, linked invoices should be marked paid."""
    v = await _make_vendor(admin_client, "VND-PA-PROC-01")
    po = await _make_approved_po(admin_client, v["id"])

    # Create and match an invoice
    inv = await admin_client.post(INV_URL, json={
        "vendor_id": v["id"],
        "vendor_invoice_number": "INV-PA-PROC",
        "amount": str(po["subtotal"]),
        "tax_amount": str(po["tax_amount"]),
        "currency": "CAD",
        "invoice_date": "2026-03-20",
        "due_date": "2026-04-19",
    })
    inv_id = inv.json()["id"]
    await admin_client.post(f"{INV_URL}/{inv_id}/match", json={"po_id": po["id"]})

    # Create PA with invoice link
    pa = await _create_pa(admin_client, po["id"],
                          invoice_ids=[inv_id],
                          subtotal=str(po["subtotal"]),
                          tax_amount=str(po["tax_amount"]))
    pid = pa["id"]

    # Full approval + process
    await admin_client.post(f"{PA_URL}/{pid}/action", json={"action": "submit"})
    await admin_client.post(f"{PA_URL}/{pid}/action", json={"action": "approve"})
    await admin_client.post(f"{PA_URL}/{pid}/action", json={"action": "approve"})
    r = await admin_client.post(f"{PA_URL}/{pid}/action", json={"action": "process"})
    assert r.json()["status"] == "processed"

    # Invoice should now be paid
    inv_resp = await admin_client.get(f"{INV_URL}/{inv_id}")
    assert inv_resp.json()["status"] == "paid"


@pytest.mark.asyncio
async def test_return_pa(admin_client):
    v = await _make_vendor(admin_client, "VND-PA-RET-01")
    po = await _make_approved_po(admin_client, v["id"])
    pa = await _create_pa(admin_client, po["id"])
    pid = pa["id"]
    await admin_client.post(f"{PA_URL}/{pid}/action", json={"action": "submit"})
    r = await admin_client.post(f"{PA_URL}/{pid}/action", json={"action": "return"})
    assert r.json()["status"] == "returned"


@pytest.mark.asyncio
async def test_pa_approval_events(admin_client):
    v = await _make_vendor(admin_client, "VND-PA-EVT-01")
    po = await _make_approved_po(admin_client, v["id"])
    pa = await _create_pa(admin_client, po["id"])
    pid = pa["id"]
    await admin_client.post(f"{PA_URL}/{pid}/action", json={"action": "submit"})

    r = await admin_client.get(f"{PA_URL}/{pid}/events")
    assert r.status_code == 200
    assert any(e["action"] == "submit" for e in r.json())


# ── Prepayment ─────────────────────────────────────────────────────────────────

async def _approve_pa(client, pa_id):
    """Drive a PA through submit → approve → approve to reach 'approved'."""
    await client.post(f"{PA_URL}/{pa_id}/action", json={"action": "submit"})
    await client.post(f"{PA_URL}/{pa_id}/action", json={"action": "approve"})
    r = await client.post(f"{PA_URL}/{pa_id}/action", json={"action": "approve"})
    return r


async def _make_prepayment(client, po_id, subtotal="100.00", tax_amount="0.00"):
    """Create a prepayment PA. Its payment_amount (= subtotal+tax) is the prepaid
    amount the later settlement reconciles against."""
    pa = await _create_pa(client, po_id,
                          pa_type="prepayment",
                          prepayment_pct="50",
                          expected_settlement_date="2026-05-01",
                          subtotal=subtotal, tax_amount=tax_amount)
    assert pa["pa_type"] == "prepayment"
    assert pa["settlement_status"] == "pending"
    return pa


# ── Receipt gate (3-way match required for non-prepayment PAs) ────────────────

@pytest.mark.asyncio
async def test_regular_pa_blocked_without_three_way(admin_client):
    v = await _make_vendor(admin_client, "VND-GATE-BLOCK")
    po = await _make_bare_po(admin_client, v["id"])          # 无 matched 发票 → 无 3-way
    r = await admin_client.post(PA_URL, json=_pa_payload(po["id"]))   # 不带 override 标志
    assert r.status_code == 422, r.text
    assert "goods receipt" in r.json()["detail"].lower() or "3-way" in r.json()["detail"]


@pytest.mark.asyncio
async def test_regular_pa_allowed_with_three_way(admin_client, test_engine):
    v = await _make_vendor(admin_client, "VND-GATE-OK")
    po = await _make_three_way_po(admin_client, test_engine, v["id"])
    r = await admin_client.post(PA_URL, json=_pa_payload(po["id"]))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_prepayment_pa_exempt_from_gate(admin_client):
    v = await _make_vendor(admin_client, "VND-GATE-PREPAY")
    po = await _make_bare_po(admin_client, v["id"])          # 无 3-way
    r = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], pa_type="prepayment", prepayment_pct="50",
        expected_settlement_date="2026-05-01", subtotal="100.00", tax_amount="0.00"))
    assert r.status_code == 201, r.text                       # 预付豁免


@pytest.mark.asyncio
async def test_override_with_permission_persists(admin_client):
    v = await _make_vendor(admin_client, "VND-GATE-OVR")
    po = await _make_bare_po(admin_client, v["id"])          # 无 3-way,但 admin 有 pa_override_receipt
    r = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], receipt_override=True, receipt_override_reason="urgent freight in transit"))
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["receipt_override"] is True
    assert data["receipt_override_reason"] == "urgent freight in transit"
    assert data["receipt_override_by"] is not None


@pytest.mark.asyncio
async def test_override_requires_reason(admin_client):
    v = await _make_vendor(admin_client, "VND-GATE-NOREASON")
    po = await _make_bare_po(admin_client, v["id"])
    r = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], receipt_override=True, receipt_override_reason="   "))   # 空白理由
    assert r.status_code == 422, r.text
    assert "reason" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_settlement_payment_amount_is_net_of_prepayment(admin_client):
    # prepaid 100; final invoice 500; applied 100 → net payable 400
    v = await _make_vendor(admin_client, "VND-PA-NET-01")
    po = await _make_po(admin_client, v["id"])
    prepay = await _make_prepayment(admin_client, po["id"], subtotal="100.00")

    r = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], pa_type="settlement",
        prepayment_pa_id=prepay["id"],
        subtotal="500.00", tax_amount="0.00",
        prepayment_applied="100.00",
        receipt_override=True, receipt_override_reason="settlement test setup",
    ))
    assert r.status_code == 201, r.text
    body = r.json()
    assert float(body["payment_amount"]) == pytest.approx(400.0)
    assert body["status"] == "draft"  # net>0 → normal approval flow


@pytest.mark.asyncio
async def test_settlement_balance_marks_prepayment_settled(admin_client, test_engine):
    """net>0 settlement: approved-hook calls mark_prepayment_settled (PA /action
    delegates to approval-api, unavailable in unit DB — so call the helper directly).
    Variance = final − prepaid = 500 − 100 = 400."""
    import uuid as _uuid
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.crud import pa as pa_crud

    v = await _make_vendor(admin_client, "VND-PA-SET-BAL-01")
    po = await _make_po(admin_client, v["id"])
    prepay = await _make_prepayment(admin_client, po["id"], subtotal="100.00")

    s = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], pa_type="settlement",
        prepayment_pa_id=prepay["id"],
        subtotal="500.00", tax_amount="0.00",
        prepayment_applied="100.00",
        receipt_override=True, receipt_override_reason="settlement test setup",
    ))
    assert s.status_code == 201, s.text
    settlement_id = _uuid.UUID(s.json()["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        settlement_pa = await pa_crud.get_by_id(db, settlement_id)
        await pa_crud.mark_prepayment_settled(db, settlement_pa, actor_id=settlement_pa.created_by)
        await db.commit()

    async with factory() as db:
        orig = await pa_crud.get_by_id(db, _uuid.UUID(prepay["id"]))
        assert orig.settlement_status == "settled"
        assert float(orig.settlement_variance) == pytest.approx(400.0)
        assert orig.settled_by == settlement_pa.created_by


@pytest.mark.asyncio
async def test_settlement_net_zero_exact_auto_reconciles(admin_client, test_engine):
    """net==0 & variance==0 (final == prepaid): no payment, no approval — creating
    the settlement auto-reconciles the prepayment immediately."""
    import uuid as _uuid
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.crud import pa as pa_crud

    v = await _make_vendor(admin_client, "VND-PA-SET-EXACT-01")
    po = await _make_po(admin_client, v["id"])
    prepay = await _make_prepayment(admin_client, po["id"], subtotal="500.00")

    # final 500, applied 500 → net 0, variance 0
    s = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], pa_type="settlement",
        prepayment_pa_id=prepay["id"],
        subtotal="500.00", tax_amount="0.00",
        prepayment_applied="500.00",
        receipt_override=True, receipt_override_reason="settlement test setup",
    ))
    assert s.status_code == 201, s.text
    body = s.json()
    assert float(body["payment_amount"]) == pytest.approx(0.0)
    assert body["status"] == "processed"  # auto-reconciled, no payment step

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        orig = await pa_crud.get_by_id(db, _uuid.UUID(prepay["id"]))
        assert orig.settlement_status == "settled"
        assert float(orig.settlement_variance) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_settlement_net_zero_overpaid_needs_confirmation(admin_client, test_engine):
    """net==0 but overpaid (prepaid 500 > final 400): does NOT auto-reconcile —
    a confirm task gates it; confirm endpoint then reconciles with a credit-note note."""
    import uuid as _uuid
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.crud import pa as pa_crud

    v = await _make_vendor(admin_client, "VND-PA-SET-OVER-01")
    po = await _make_po(admin_client, v["id"])
    prepay = await _make_prepayment(admin_client, po["id"], subtotal="500.00")

    # final 400, applied 400 → net 0; prepaid 500 → variance −100 (overpaid)
    s = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], pa_type="settlement",
        prepayment_pa_id=prepay["id"],
        subtotal="400.00", tax_amount="0.00",
        prepayment_applied="400.00",
        receipt_override=True, receipt_override_reason="settlement test setup",
    ))
    assert s.status_code == 201, s.text
    sid = s.json()["id"]
    assert s.json()["status"] == "submitted"  # awaiting finance confirmation

    # prepayment not yet settled
    async with async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)() as db:
        orig = await pa_crud.get_by_id(db, _uuid.UUID(prepay["id"]))
        assert orig.settlement_status == "pending"

    # finance confirms → reconcile with credit-note note, no payment
    c = await admin_client.post(f"{PA_URL}/{sid}/confirm-settlement")
    assert c.status_code == 200, c.text
    assert c.json()["status"] == "processed"

    async with async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)() as db:
        orig = await pa_crud.get_by_id(db, _uuid.UUID(prepay["id"]))
        assert orig.settlement_status == "settled"
        assert float(orig.settlement_variance) == pytest.approx(-100.0)
        assert "credit note" in (orig.settlement_note or "").lower()


@pytest.mark.asyncio
async def test_settlement_applied_exceeds_invoice_rejected(admin_client):
    # applied 200 > final invoice 100 → net negative → 422
    v = await _make_vendor(admin_client, "VND-PA-OVER-01")
    po = await _make_po(admin_client, v["id"])
    prepay = await _make_prepayment(admin_client, po["id"], subtotal="500.00")

    r = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], pa_type="settlement",
        prepayment_pa_id=prepay["id"],
        subtotal="100.00", tax_amount="0.00",
        prepayment_applied="200.00",
    ))
    assert r.status_code == 422
    detail = r.json()["detail"].lower()
    assert "net" in detail or "credit" in detail


@pytest.mark.asyncio
async def test_settlement_requires_valid_prepayment_ref(admin_client):
    v = await _make_vendor(admin_client, "VND-PA-SET-REF-01")
    po = await _make_po(admin_client, v["id"])
    # Missing prepayment_pa_id → 422
    r = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], pa_type="settlement",
        subtotal="100.00", tax_amount="0.00", prepayment_applied="0.00",
    ))
    assert r.status_code == 422


# ── action='process' → finance-api payment executor (vendor credit netting) ───
#
# The single-payment path is NOT a second-class citizen of the batch runner:
# it forwards to the same `payment_execute.execute()` and therefore takes the
# same automatic FIFO vendor-credit default. Until this wave it could not carry
# the operator's choice at all, so the PA Process dialog's netting preview had
# nowhere to send a deselection. These tests pin the wire contract.
#
# They call the route function directly with `pa_crud.get_by_id` and
# `finance_client.execute_payment` stubbed, rather than going through the HTTP
# client: everything between a POST and this branch (vendor create → PO create
# → PO approval) needs approval-api and identity tables this unit DB does not
# have, which is why the HTTP-driven tests above already fail here. The
# forwarding of body.credit_ids is what is under test, and it is fully
# exercised this way.

class _FakePa:
    """Minimal stand-in for the PaymentApplication the route loads. Only the
    attributes pa_action touches on the 'process' path."""
    def __init__(self):
        import uuid as _uuid
        self.id = _uuid.uuid4()
        self.po_id = _uuid.uuid4()      # non-None: a NULL po_id is an OA Direct PA → 404
        self.pa_type = "regular"
        self.pa_number = "PA-TEST-0001"
        self.status = "approved"


class _FakeDb:
    async def refresh(self, _obj):
        return None


async def _run_pa_action(monkeypatch, **action_body):
    """Invoke the pa_action route on the 'process' branch and return the kwargs
    finance_client.execute_payment was called with."""
    import app.api.v1.pa as pa_api
    from app.schemas.pa import PaActionRequest

    pa = _FakePa()
    captured: dict = {}

    async def _fake_get_by_id(_db, _pa_id):
        return pa

    async def _fake_execute_payment(**kwargs):
        captured.update(kwargs)
        return {"new_status": "processed"}

    monkeypatch.setattr(pa_api.pa_crud, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(pa_api.finance_client, "execute_payment", _fake_execute_payment)

    returned = await pa_api.pa_action(
        pa_id=pa.id,
        body=PaActionRequest(action="process", **action_body),
        db=_FakeDb(),
        user={"sub": str(pa.id), "role": "ap_clerk"},
        token="fake-token",
    )
    assert returned is pa
    return captured


@pytest.mark.asyncio
async def test_process_omits_credit_ids_when_the_dialog_was_untouched(monkeypatch):
    """None must reach finance-api as an ABSENT key, not as an empty list.
    credit_ids is three-valued: absent = "apply the automatic FIFO default",
    [] = "apply nothing". Collapsing the two would make an untouched Process
    dialog silently pay the gross."""
    captured = await _run_pa_action(monkeypatch)
    assert captured["credit_ids"] is None


@pytest.mark.asyncio
async def test_process_forwards_an_explicit_credit_selection(monkeypatch):
    """The operator deselected one of the suggested credits: exactly the
    surviving ids go through, so finance-api re-runs capped FIFO over them
    under row locks."""
    import uuid as _uuid
    keep = [str(_uuid.uuid4()), str(_uuid.uuid4())]
    captured = await _run_pa_action(monkeypatch, credit_ids=keep)
    assert [str(c) for c in captured["credit_ids"]] == keep


@pytest.mark.asyncio
async def test_process_forwards_an_empty_credit_selection(monkeypatch):
    """[] is a real instruction — "pay this one in full, apply no credit" — and
    must survive the trip rather than being normalised back to the default."""
    captured = await _run_pa_action(monkeypatch, credit_ids=[])
    assert captured["credit_ids"] == []


def test_pa_action_request_accepts_credit_ids():
    """The schema itself: without this field the route has nothing to forward,
    and FastAPI would silently drop the key the Process dialog sends."""
    import uuid as _uuid
    from app.schemas.pa import PaActionRequest

    assert PaActionRequest(action="process").credit_ids is None
    assert PaActionRequest(action="process", credit_ids=[]).credit_ids == []
    cid = str(_uuid.uuid4())
    assert [str(c) for c in
            PaActionRequest(action="process", credit_ids=[cid]).credit_ids] == [cid]


@pytest.mark.asyncio
async def test_finance_client_puts_credit_ids_on_the_wire_only_when_given(monkeypatch):
    """The payload builder itself: `if credit_ids:` would collapse [] into
    "omitted" and net a payment the operator asked to pay in full. This calls
    the real `execute_payment` (only httpx.AsyncClient is faked, so the actual
    dict-construction code runs) and inspects the JSON body that would have
    gone on the wire to finance-api's POST /payments/execute — the one hop
    where None-vs-[] could silently collapse and the three route-level tests
    above (which stub execute_payment out entirely) cannot see."""
    import uuid as _uuid

    from app.services import finance_client

    captured: dict = {}

    class _FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            return {"new_status": "processed"}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["payload"] = json
            return _FakeResponse()

    monkeypatch.setattr(finance_client.httpx, "AsyncClient", _FakeAsyncClient)

    doc_id = _uuid.uuid4()

    # credit_ids=None -> key absent from the body entirely.
    await finance_client.execute_payment(doc_kind="pa", doc_id=doc_id, bearer_token="tok")
    assert "credit_ids" not in captured["payload"]

    # credit_ids=[] -> present, empty — "apply nothing", not "omitted".
    await finance_client.execute_payment(
        doc_kind="pa", doc_id=doc_id, bearer_token="tok", credit_ids=[])
    assert captured["payload"]["credit_ids"] == []

    # credit_ids=[uuid] -> present, each id stringified.
    cid = _uuid.uuid4()
    await finance_client.execute_payment(
        doc_kind="pa", doc_id=doc_id, bearer_token="tok", credit_ids=[cid])
    assert captured["payload"]["credit_ids"] == [str(cid)]
