"""Invoice ↔ Agreement matching (Phase 1A: manual route, no slips)."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agreement import PurchaseAgreement
from app.models.invoice import Invoice
from app.models.task import Task
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio

INV_URL = "/api/v1/invoices"


async def test_invoice_carries_agreement_link_columns(test_engine):
    """The new columns exist, default correctly, and round-trip."""
    vendor_id, vendor_name, user_id = await seed_vendor_and_user(test_engine)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        inv = Invoice(
            internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_invoice_number="PA-STMT-202607",
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            amount=Decimal("1000.00"),
            tax_amount=Decimal("130.00"),
            total_amount=Decimal("1130.00"),
            invoice_date=date(2026, 7, 31),
            due_date=date(2026, 8, 30),
            uploaded_by=user_id,
            line_items=[],
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)

        assert inv.agreement_id is None
        assert inv.agreement_number is None
        assert inv.match_route is None
        assert inv.match_route_auto is False
        assert inv.legacy_settlement is False
        assert inv.legacy_settlement_reason is None


async def _make_active_agreement(test_engine, vendor_id, created_by, **over):
    """Insert an active agreement directly — bypasses the approval chain, which
    needs a running approval-api this suite does not have."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    vendor_uuid = vendor_id if isinstance(vendor_id, uuid.UUID) else uuid.UUID(str(vendor_id))
    creator_uuid = created_by if isinstance(created_by, uuid.UUID) else uuid.UUID(str(created_by))
    fields = dict(
        number=f"AGR-202608-{uuid.uuid4().hex[:4]}",
        title="Test house account",
        agreement_type="house_account",
        vendor_id=vendor_uuid,
        vendor_name="Test Vendor",
        valid_from=date.today() - timedelta(days=30),
        valid_to=date.today() + timedelta(days=30),
        status="active",
        created_by=creator_uuid,
    )
    fields.update(over)
    async with factory() as db:
        agr = PurchaseAgreement(**fields)
        db.add(agr)
        await db.commit()
        await db.refresh(agr)
        return agr


async def _upload_invoice(client, vendor_id, amount="1000.00"):
    r = await client.post(INV_URL, json={
        "vendor_id": str(vendor_id),
        "vendor_invoice_number": f"STMT-{uuid.uuid4().hex[:6]}",
        "amount": amount,
        "tax_amount": "0.00",
        "currency": "CAD",
        "invoice_date": "2026-07-31",
        "due_date": "2026-08-30",
        "line_items": [{"description": "Monthly statement", "quantity": "1",
                        "unit_price": amount, "line_total": amount}],
    })
    r.raise_for_status()
    return r.json()


async def test_agreement_candidates_returns_active_same_vendor(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id)

    r = await admin_client.get(f"{INV_URL}/{inv['id']}/agreement-candidates")
    assert r.status_code == 200, r.text
    assert str(agr.id) in [i["id"] for i in r.json()["items"]]


async def test_agreement_candidates_excludes_draft_and_cancelled(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    draft = await _make_active_agreement(test_engine, vendor_id, user_id, status="draft")
    cancelled = await _make_active_agreement(test_engine, vendor_id, user_id, status="cancelled")
    inv = await _upload_invoice(admin_client, vendor_id)

    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(draft.id) not in ids
    assert str(cancelled.id) not in ids


async def test_agreement_candidates_excludes_other_vendors(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    other_vendor_id, _other_name, _other_user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Other Vendor")

    other = await _make_active_agreement(test_engine, other_vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id)

    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(other.id) not in ids


async def test_expired_agreement_inside_grace_is_a_candidate(admin_client, test_engine):
    """8/31 expiry, 9/3 statement — the month's bill always lands after expiry."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id,
        status="expired",
        valid_from=date.today() - timedelta(days=90),
        valid_to=date.today() - timedelta(days=5),
        grace_days=30,
    )
    inv = await _upload_invoice(admin_client, vendor_id)
    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(agr.id) in ids


async def test_expired_agreement_past_grace_is_not_a_candidate(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id,
        status="expired",
        valid_from=date.today() - timedelta(days=200),
        valid_to=date.today() - timedelta(days=100),
        grace_days=30,
    )
    inv = await _upload_invoice(admin_client, vendor_id)
    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(agr.id) not in ids


# ── match() agreement branch ────────────────────────────────────────────────

async def test_match_to_agreement_sets_route_and_consumes(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id,
                                       not_to_exceed=Decimal("50000.00"))
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id),
        "legacy_settlement_reason": "Backlog statement, paper slips held by Finance",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "matched"
    assert body["agreement_id"] == str(agr.id)
    assert body["agreement_number"] == agr.number
    assert body["match_route"] == "agreement"
    assert body["match_route_auto"] is False
    assert body["po_id"] is None
    assert Decimal(body["variance"]) == Decimal("0")
    assert body["legacy_settlement"] is True

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert fresh.consumed_amount == Decimal("1000.00")


async def test_match_to_agreement_requires_a_reason_in_1a(admin_client, test_engine):
    """1A has no pickup slips, so every agreement match is a legacy settlement
    and must carry a reason. 1B replaces this with real slip reconciliation."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id)

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match",
                                json={"agreement_id": str(agr.id)})
    assert r.status_code == 422
    assert "reason" in r.text.lower()


async def test_match_to_agreement_rejects_vendor_mismatch(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    other_vendor_id, _other_name, _other_user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Other Vendor")
    agr = await _make_active_agreement(test_engine, other_vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id)

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "x"})
    assert r.status_code == 422
    assert "vendor" in r.text.lower()


async def test_match_to_draft_agreement_is_refused(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id, status="draft")
    inv = await _upload_invoice(admin_client, vendor_id)
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "x"})
    assert r.status_code == 422


async def test_agreement_match_does_not_block_when_over_nte(admin_client, test_engine):
    """NTE warns, it does not block (user decision). An invoice that pushes
    consumed_amount past the ceiling must still match."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id,
                                       not_to_exceed=Decimal("100.00"))
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    assert r.status_code == 200, r.text

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert fresh.consumed_amount == Decimal("1000.00")   # over the 100.00 ceiling, allowed


async def test_rematch_from_agreement_to_po_releases_consumption(admin_client, test_engine):
    """Re-matching must not leave the agreement's consumed_amount inflated.

    The HTTP /match endpoint refuses a second call once status="matched" (409 —
    a separate, pre-existing gate unrelated to this task), so calling POST
    /match twice through admin_client would "pass" even with a buggy `+=`
    accumulator, since the second call would simply never run. To genuinely
    exercise re-match idempotency this drives app.crud.invoice.match() directly
    against the same invoice/agreement pair twice, the way rematch_from_existing
    does internally for the PO route.
    """
    from app.crud.invoice import match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, legacy_settlement_reason="first pass"), matched_by=user_id)
    # Re-match to the SAME agreement — consumption must not double-count.
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, legacy_settlement_reason="corrected"), matched_by=user_id)

    async with factory() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert fresh.consumed_amount == Decimal("1000.00")


async def test_rematch_moves_consumption_between_agreements(admin_client, test_engine):
    """If an invoice moves from one agreement to another, BOTH must be
    recomputed — the old one releases the amount, the new one picks it up."""
    from app.crud.invoice import match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr_a = await _make_active_agreement(test_engine, vendor_id, user_id)
    agr_b = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr_a.id, legacy_settlement_reason="first pass"), matched_by=user_id)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr_b.id, legacy_settlement_reason="moved to B"), matched_by=user_id)

    async with factory() as db:
        fresh_a = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr_a.id))).scalar_one()
        fresh_b = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr_b.id))).scalar_one()
    assert fresh_a.consumed_amount == Decimal("0")
    assert fresh_b.consumed_amount == Decimal("1000.00")


async def test_agreement_match_does_not_notify_create_pa_requester(admin_client, test_engine):
    """A PO with no PR previously broadcast an acknowledgement task to 59 people,
    twice (see project_uniops_gr_no_pr_requester_broadcast). An agreement match
    has po_id=None, so there is no PR requester to notify — assert no create_pa
    task row appears. Compared as a before/after delta so the assertion holds
    regardless of create_pa rows left behind by other tests in this
    session-scoped test_engine."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        before = len((await db.execute(
            select(Task.id).where(Task.type == "create_pa")
        )).all())

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    assert r.status_code == 200, r.text

    async with factory() as db:
        after = len((await db.execute(
            select(Task.id).where(Task.type == "create_pa")
        )).all())
    assert after == before
