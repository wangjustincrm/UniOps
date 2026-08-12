"""Task 6/7 (this gate itself is untouched — app/api/v1/pa.py is not in Task
6's file list): the PA-side gate for house_account agreements — a payment
application cannot be raised against an invoice that has neither agreement-
receipt evidence nor an explicit legacy-settlement flag.

Phase 1A settled every house_account invoice with no evidence at all
(legacy_settlement=True, reason required). Task 5 gave house_account a real
evidence path — agreement receipts — so an invoice now lands in one of two
valid states: receipt_ids non-empty (real evidence), or legacy_settlement=True
(an explicit, reasoned admission that there is none). This is where payment
gets gated on that: _validate_agreement_pa_invoices (app/api/v1/pa.py) must
refuse any house_account invoice caught in neither state, while continuing to
let recurring/milestone PAs through untouched — they were never in scope for
receipt evidence at all.

Task 6 pulled BOTH of those states out of /match (matching a house_account
invoice to an agreement is now pure linkage — see test_receipt_match.py). Mounting
a receipt and declaring a legacy settlement move to the invoice detail page
(Task 7/8), which doesn't exist yet, so the two tests below that need the
invoice to actually HOLD one of those states now seed it directly on the row —
the same "write the target shape directly, the request field doesn't exist
yet" pattern test_agreement_invoice_match.py's
test_route_switch_to_po_releases_claimed_receipts already used before this
task. What's under test here is the GATE (pa.py), unaffected by Task 6.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import agreement_schedule as sched_crud
from app.crud import agreement_receipt as agreement_receipt_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.invoice import Invoice
from app.schemas.agreement_receipt import ReceiptCreate
from tests.test_agreement_invoice_match import _make_active_agreement, _upload_invoice
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio

PA_URL = "/api/v1/pa"


async def _create_receipt(db: AsyncSession, agr, user_id: uuid.UUID, *, amount: str = "100.00"):
    receipt = await agreement_receipt_crud.create(
        db, agr,
        ReceiptCreate(
            receipt_date=date(2026, 7, 15), receipt_ref=None,
            amount=Decimal(amount), tax_amount=Decimal("0.00"), total_amount=Decimal(amount),
            received_by=user_id, missing_receipt_reason=None, notes=None,
        ),
        created_by=user_id,
    )
    await db.commit()
    await db.refresh(receipt)
    return receipt


async def test_house_account_pa_refused_when_invoice_has_neither_receipts_nor_legacy(
        admin_client, test_engine):
    """Neither real evidence nor an explicit legacy admission — the 422 must
    name the offending invoice so the caller knows what to fix. Task 6: this
    is now the OUTPUT of any bare house_account /match — matching no longer
    sets legacy_settlement or receipt_ids at all, it is pure linkage — so no
    forcing is even needed to reach this state; the invoice lands here by
    default. The gate must still refuse it."""
    vendor_id, _vn, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")

    r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match", json={
        "agreement_id": str(agr.id)})
    assert r.status_code == 200, r.text
    assert r.json()["legacy_settlement"] is False

    pa = await admin_client.post(PA_URL, json={
        "title": "No evidence at all", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "100.00", "tax_amount": "0.00", "payment_amount": "100.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "100.00", "line_total": "100.00"}],
    })
    assert pa.status_code == 422, pa.text
    assert "No receipts are attached to" in pa.text
    assert inv["internal_ref"] in pa.text


async def test_house_account_pa_allowed_with_receipts(admin_client, test_engine):
    """Task 6: /match can no longer claim a receipt (receipt_ids is not a field
    on InvoiceMatchRequest — the mounting endpoint that will do this lands in
    Task 7 and doesn't exist yet). Seed the claim directly on the invoice row,
    the same shape agreement_receipt_crud.claim() plus Task 7's endpoint will
    produce, so this test keeps exercising the GATE (pa.py) and not a request
    field that no longer exists."""
    vendor_id, _vn, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")

    r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match", json={
        "agreement_id": str(agr.id)})
    assert r.status_code == 200, r.text
    assert r.json()["legacy_settlement"] is False

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00")
        db_inv = (await db.execute(select(Invoice).where(
            Invoice.id == uuid.UUID(inv["id"])))).scalar_one()
        claimed = await agreement_receipt_crud.claim(db, agr, [receipt.id], db_inv)
        db_inv.receipt_ids = [str(row.id) for row in claimed]
        await db.commit()

    # InvoiceResponse does not expose receipt_ids — resolve it via the DB, as
    # the existing suite does for schedule_id.
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        stored_receipt_ids = (await db.execute(select(Invoice.receipt_ids).where(
            Invoice.id == uuid.UUID(inv["id"])))).scalar_one()
    assert stored_receipt_ids == [str(receipt.id)]

    pa = await admin_client.post(PA_URL, json={
        "title": "Backed by a receipt", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "100.00", "tax_amount": "0.00", "payment_amount": "100.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "100.00", "line_total": "100.00"}],
    })
    assert pa.status_code == 201, pa.text


async def test_house_account_pa_allowed_for_a_legacy_settled_invoice(admin_client, test_engine):
    """Every 1A invoice in the backlog is legacy_settlement=True with no receipts
    at all — this is the case that must NOT start failing, or this change
    would retroactively block payment on the entire pre-1B backlog.

    Task 6: /match no longer sets legacy_settlement — declaring "no evidence,
    here's why" moves to the invoice detail page (Task 8), which doesn't exist
    yet. Seed the declaration directly on the row, the same shape that page
    will write, so this test keeps exercising the GATE and not a request field
    that no longer does this."""
    vendor_id, _vn, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")

    r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match", json={
        "agreement_id": str(agr.id)})
    assert r.status_code == 200, r.text
    assert r.json()["legacy_settlement"] is False

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(
            Invoice.id == uuid.UUID(inv["id"])))).scalar_one()
        db_inv.legacy_settlement = True
        db_inv.legacy_settlement_reason = "backlog statement, no receipt"
        await db.commit()

    async with factory() as db:
        stored_receipt_ids = (await db.execute(select(Invoice.receipt_ids).where(
            Invoice.id == uuid.UUID(inv["id"])))).scalar_one()
    assert stored_receipt_ids is None

    pa = await admin_client.post(PA_URL, json={
        "title": "Legacy backlog statement", "agreement_id": str(agr.id),
        "invoice_ids": [inv["id"]],
        "subtotal": "100.00", "tax_amount": "0.00", "payment_amount": "100.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "100.00", "line_total": "100.00"}],
    })
    assert pa.status_code == 201, pa.text


async def test_recurring_and_milestone_gates_are_unchanged(admin_client, test_engine):
    """The new gate is scoped to agreement_type == "house_account" only.
    Neither recurring nor milestone invoices ever get receipt_ids, and
    crud_match sets legacy_settlement=False for both (Task 5's receipt
    bookkeeping is house_account-only) — if the new gate applied to them, a
    perfectly confirmed recurring period or an accepted milestone would now
    be refused. It must not reach either type at all."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # ── recurring: confirmed period, no receipt evidence, no legacy flag ──
    vendor_id, _vn, user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Gate Unchanged Recurring Vendor")
    recurring_agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    recurring_inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    async with factory() as db:
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == recurring_agr.id))).scalar_one()
        await sched_crud.ensure_period_rows(db, fresh_agr)
        await db.commit()

    r = await admin_client.post(f"/api/v1/invoices/{recurring_inv['id']}/match", json={
        "agreement_id": str(recurring_agr.id)})
    assert r.status_code == 200, r.text
    assert r.json()["legacy_settlement"] is False

    async with factory() as db:
        schedule_id, stored_receipt_ids = (await db.execute(select(
            Invoice.schedule_id, Invoice.receipt_ids,
        ).where(Invoice.id == uuid.UUID(recurring_inv["id"])))).one()
    assert schedule_id is not None
    assert stored_receipt_ids is None
    r_confirm = await admin_client.post(
        f"/api/v1/agreements/{recurring_agr.id}/schedule/{schedule_id}/confirm")
    assert r_confirm.status_code == 200, r_confirm.text

    recurring_pa = await admin_client.post(PA_URL, json={
        "title": "Recurring, no receipt evidence", "agreement_id": str(recurring_agr.id),
        "invoice_ids": [recurring_inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert recurring_pa.status_code == 201, recurring_pa.text

    # ── milestone: matched to a schedule row, no receipt evidence, no legacy flag ──
    vendor_id2, _vn2, user_id2 = await seed_vendor_and_user(
        test_engine, vendor_name="Gate Unchanged Milestone Vendor")
    milestone_agr = await _make_active_agreement(
        test_engine, vendor_id2, user_id2, agreement_type="milestone")
    milestone_inv = await _upload_invoice(admin_client, vendor_id2, amount="500.00")

    async with factory() as db:
        row = AgreementPaymentSchedule(
            agreement_id=milestone_agr.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit", status="pending")
        db.add(row)
        await db.commit()
        await db.refresh(row)
        row_id = row.id

    r2 = await admin_client.post(f"/api/v1/invoices/{milestone_inv['id']}/match", json={
        "agreement_id": str(milestone_agr.id), "schedule_id": str(row_id)})
    assert r2.status_code == 200, r2.text
    assert r2.json()["legacy_settlement"] is False

    async with factory() as db:
        stored_receipt_ids2 = (await db.execute(select(Invoice.receipt_ids).where(
            Invoice.id == uuid.UUID(milestone_inv["id"])))).scalar_one()
    assert stored_receipt_ids2 is None

    milestone_pa = await admin_client.post(PA_URL, json={
        "title": "Milestone, no receipt evidence", "agreement_id": str(milestone_agr.id),
        "invoice_ids": [milestone_inv["id"]],
        "subtotal": "500.00", "tax_amount": "0.00", "payment_amount": "500.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "500.00", "line_total": "500.00"}],
    })
    assert milestone_pa.status_code == 201, milestone_pa.text
