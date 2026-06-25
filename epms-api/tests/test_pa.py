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
async def test_list_pas(admin_client):
    v = await _make_vendor(admin_client, "VND-PA-LIST-01")
    po = await _make_approved_po(admin_client, v["id"])
    await _create_pa(admin_client, po["id"])
    r = await admin_client.get(PA_URL)
    assert r.status_code == 200
    assert len(r.json()) >= 1


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
