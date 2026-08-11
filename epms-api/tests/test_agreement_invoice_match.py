"""Invoice ↔ Agreement matching (Phase 1A: manual route, no slips)."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.invoice import Invoice
from app.models.task import Task
from app.schemas.auth import RegisterRequest
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
        number=f"AGR-202608-T{uuid.uuid4().hex[:11]}",
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


async def _delegate_client(test_engine):
    """A 'requester'-role user, pre-authenticated — neither AP staff (not in
    _AP_ROLES) nor (by construction, never the uploader of any invoice created
    in these tests) the uploader, i.e. the caller shape that produces
    require_review=True in match_invoice(). Returns (client, user_id)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"delegate-{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass1!", full_name="Delegate Tester", role="requester"))
        await db.commit()
        await db.refresh(user)
    token = create_access_token(str(user.id), user.role)
    client = AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return client, user.id


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


async def test_rematch_to_same_agreement_is_idempotent(admin_client, test_engine):
    """Re-matching must not leave the agreement's consumed_amount inflated.

    (Renamed from test_rematch_from_agreement_to_po_releases_consumption —
    that name promised agreement→PO coverage this test never exercised; see
    the real agreement→PO test below, added in the same review round.)

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


async def test_rematch_from_agreement_to_po_releases_consumption(admin_client, test_engine):
    """The PO branch must be the mirror image of the agreement branch: clear
    agreement_id/agreement_number/legacy_settlement, set match_route="po", and
    recompute the OLD agreement's consumed_amount back down. This exercises a
    real agreement→PO transition (code review finding: the previous test with
    this name only re-matched to the SAME agreement, so this scenario had no
    coverage at all).

    Driven via app.crud.invoice.match() directly, not the HTTP endpoint,
    because POST /match 409s on an already-"matched" invoice (which every
    agreement match produces) — same reasoning as
    test_rematch_to_same_agreement_is_idempotent above. Unlike
    _match_to_agreement, the PO branch of match() does not self-commit, so
    each step below commits explicitly to make the change visible to the
    next session.
    """
    from app.crud.invoice import match as crud_match
    from app.schemas.invoice import AllocationInput, InvoiceMatchRequest

    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])
    inv_line_id = uuid.UUID(inv["line_items"][0]["id"])

    po = (await admin_client.post("/api/v1/po", json={
        "title": "Agreement rematch PO", "type": 2, "vendor_id": str(vendor_id),
        "currency": "CAD", "tax_rate": "0.00",
        "line_items": [{"description": "Widget", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00"}],
    }))
    assert po.status_code in (200, 201), po.text
    po = po.json()
    po_line_id = uuid.UUID(po["line_items"][0]["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, legacy_settlement_reason="first pass"), matched_by=user_id)

    async with factory() as db:
        agr_after_first_match = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert agr_after_first_match.consumed_amount == Decimal("1000.00")

    # Move the invoice to the PO route (exact-match allocation, zero variance).
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(allocations=[
            AllocationInput(invoice_line_id=inv_line_id, po_id=uuid.UUID(po["id"]),
                            po_line_id=po_line_id, allocated_amount=Decimal("1000.00"),
                            allocated_tax=Decimal("0.00")),
        ]), matched_by=user_id)
        await db.commit()

    async with factory() as db:
        fresh_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()

    assert fresh_inv.agreement_id is None
    assert fresh_inv.agreement_number is None
    assert fresh_inv.match_route == "po"
    assert fresh_inv.legacy_settlement is False
    assert fresh_inv.legacy_settlement_reason is None
    assert fresh_inv.po_id == uuid.UUID(po["id"])
    assert fresh_agr.consumed_amount == Decimal("0")


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
    has po_id=None, so there is no PR requester to notify — assert no task row
    appears. Compared as a before/after delta so the assertion holds regardless
    of task rows left behind by other tests in this session-scoped test_engine.

    Deliberately widened beyond type=="create_pa" (code review MINOR finding):
    if a future change keeps po_id set on an agreement-matched invoice for
    "traceability", _on_invoice_matched sees gr_id is None → three_way=False →
    routes to _create_or_renotify_confirm_receipt instead, which creates an
    assigned_user_id=None "warehouse_staff" pool broadcast — the same shape of
    incident, different task type. Filtering on document_type=="po" (the field
    both create_pa and confirm_receipt tasks share) catches either."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        before = len((await db.execute(
            select(Task.id).where(Task.document_type == "po")
        )).all())

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    assert r.status_code == 200, r.text

    async with factory() as db:
        after = len((await db.execute(
            select(Task.id).where(Task.document_type == "po")
        )).all())
    assert after == before


# ── consumed_amount refresh outside match() (code review IMPORTANT findings) ──
#
# The two paths below are the only other places an agreement invoice's
# footprint on consumed_amount can change after the initial match: editing its
# amount, and hard-deleting it. Neither goes through match() again, so
# _recompute_consumed had to be wired into crud.invoice.update() and
# crud.invoice.delete() directly, not just _match_to_agreement().

async def test_agreement_invoice_amount_edit_recomputes_consumed_amount(admin_client, test_engine):
    """AP corrects a house-account statement's amount via PATCH after it's
    already matched. rematch_from_existing (api/v1/invoices.py) does NOT fire
    for an agreement invoice — it only re-runs match() when po_id or gr_ids
    are already set, and an agreement invoice carries neither — so without a
    recompute inside crud.invoice.update() itself, consumed_amount would keep
    reporting the ORIGINAL amount forever after an edit, under-reporting the
    NTE ceiling."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    assert r.status_code == 200, r.text

    r2 = await admin_client.patch(f"{INV_URL}/{inv['id']}", json={"amount": "10000.00"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["total_amount"] == "10000.00"

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert fresh.consumed_amount == Decimal("10000.00")


async def test_agreement_invoice_delete_recomputes_consumed_amount(admin_client, test_engine):
    """A hard-deleted invoice must not leave the agreement's consumed_amount
    permanently inflated by a row that no longer exists — the FK
    (purchase_agreements.id ← invoices.agreement_id) is ondelete="RESTRICT" on
    the AGREEMENT side only, so nothing at the database level stops this.

    crud.invoice.delete() only permits deleting an invoice whose status is
    "unmatched" or "exception"; an agreement match always lands on "matched"
    (variance is definitionally 0), so this specific combination — deletable
    status + agreement_id still set — is not reachable through today's API.
    The DB is nudged directly into that state to exercise the crud-layer
    guarantee defensively, the same way the code review asked for it: the
    invariant must hold in crud.invoice.delete() regardless of which future
    caller reaches it."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    assert r.status_code == 200, r.text

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        db_inv.status = "exception"   # synthetic: makes delete() permit this row
        await db.commit()

    r2 = await admin_client.delete(f"{INV_URL}/{inv['id']}")
    assert r2.status_code == 204, r2.text

    async with factory() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert fresh.consumed_amount == Decimal("0")


# ── require_review on the agreement route (code review finding, 2026-08-07) ──
#
# match_invoice() computes require_review = not (is_ap or is_uploader) and
# passes it into crud.invoice.match(). The PO branch honours it (match_review
# when a delegate's match shows non-zero variance); _match_to_agreement
# originally ignored it outright, so a delegated (non-AP, non-uploader)
# caller completed an agreement match terminally — the ONE route with zero
# receipt evidence had the WEAKEST control, backwards from what
# require_review exists to guard against.

async def test_agreement_match_by_delegate_requires_review(admin_client, test_engine):
    """A delegate (holds an open match_invoice task, is neither AP staff nor
    the uploader) matching an invoice to an agreement must land in
    match_review, not matched — mirroring the PO route's require_review gate.
    The agreement route has no PO line to independently verify against (that
    is the entire point of legacy_settlement), so unlike the PO route there is
    no "zero variance, objectively confirmed" case to exempt: require_review
    alone decides."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    delegate_client, delegate_id = await _delegate_client(test_engine)
    try:
        r_assign = await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                           json={"user_id": str(delegate_id)})
        assert r_assign.status_code == 200, r_assign.text

        r = await delegate_client.post(f"{INV_URL}/{inv['id']}/match", json={
            "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "match_review"
        assert body["match_route"] == "agreement"
        assert body["legacy_settlement"] is True
        assert Decimal(body["variance"]) == Decimal("0")
    finally:
        await delegate_client.aclose()

    # The endpoint's review-task creation (fires on result.status=="match_review"
    # AND the caller held an open match_invoice task) is route-agnostic — it
    # should already fire for the agreement route with no endpoint change.
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        review_task = (await db.execute(select(Task).where(
            Task.type == "review_match", Task.document_type == "invoice",
            Task.document_id == uuid.UUID(inv["id"]), Task.is_completed.is_(False),
        ))).scalar_one_or_none()
        agr_fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert review_task is not None
    assert review_task.assigned_role == "ap_clerk"
    # No-evidence settlement: the review description must say so and quote
    # the reason (Task 5 round-1 review fix, Important #2 — this branch is
    # unchanged from before Task 5; the NEW branches are covered by
    # test_agreement_match_by_delegate_with_slips_review_description below).
    assert "as a legacy settlement (no receipt evidence): backlog" in review_task.description
    # A pending-review invoice still reserves against the ceiling — the same
    # "every linked invoice counts, no status filter" contract _recompute_
    # consumed already implements; it isn't a real spend yet, but it also isn't
    # released until approved or rejected.
    assert agr_fresh.consumed_amount == Decimal("1000.00")


async def test_agreement_match_by_delegate_with_slips_review_description(admin_client, test_engine):
    """Task 5 round-1 review fix (Important #2): a delegate matching a
    house_account invoice WITH claimed pickup slips must not get the "as a
    legacy settlement (no receipt evidence): None" description — that invoice
    has real evidence, and the old unconditional wording asserted the exact
    opposite of what happened. The review description must say slips were
    claimed, and must not claim "no receipt evidence" or print "None"."""
    from tests.test_slip_match import _create_slip

    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Slip Review Text Vendor")
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        slip = await _create_slip(db, agr, user_id, amount="100.00")

    delegate_client, delegate_id = await _delegate_client(test_engine)
    try:
        r_assign = await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                           json={"user_id": str(delegate_id)})
        assert r_assign.status_code == 200, r_assign.text

        r = await delegate_client.post(f"{INV_URL}/{inv['id']}/match", json={
            "agreement_id": str(agr.id), "slip_ids": [str(slip.id)]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "match_review"
        assert body["legacy_settlement"] is False
    finally:
        await delegate_client.aclose()

    async with factory() as db:
        review_task = (await db.execute(select(Task).where(
            Task.type == "review_match", Task.document_type == "invoice",
            Task.document_id == uuid.UUID(inv["id"]), Task.is_completed.is_(False),
        ))).scalar_one_or_none()
    assert review_task is not None
    assert "no receipt evidence" not in review_task.description
    assert "None" not in review_task.description
    assert "1 claimed pickup slip" in review_task.description


async def test_agreement_match_by_ap_completes_terminally(admin_client, test_engine):
    """The other half of the same gate: an AP caller (admin_client's role is
    system_admin, in _AP_ROLES) matching to an agreement must NOT be routed
    through review — require_review is False for AP/uploader callers on
    either route. (Already incidentally covered by
    test_match_to_agreement_sets_route_and_consumes; this test exists to make
    the require_review=False side of the gate an explicit, named assertion.)"""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"


async def test_agreement_match_review_reject_releases_consumed_amount(admin_client, test_engine):
    """Proactive follow-on fix: enabling match_review on the agreement route
    makes a NEW state reachable that was previously impossible — an invoice
    with agreement_id still set but status back at "unmatched" after an AP
    clerk rejects the review. review_match()'s reject branch, for the PO
    route, deliberately KEEPS po_id/allocations "for reference" (a rejected
    match was never counted anywhere else). The agreement route can't reuse
    that convention as-is: _recompute_consumed sums every invoice with
    agreement_id still set, with no status filter, so leaving agreement_id in
    place after reject would leave a REJECTED, non-payable invoice inflating
    the NTE ceiling forever. review_match() now detaches agreement_id/
    agreement_number/match_route/legacy_settlement on reject and recomputes
    the released agreement, the same cleanup match()'s PO branch already
    performs on a route switch."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    delegate_client, delegate_id = await _delegate_client(test_engine)
    try:
        await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                json={"user_id": str(delegate_id)})
        r = await delegate_client.post(f"{INV_URL}/{inv['id']}/match", json={
            "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "match_review"
    finally:
        await delegate_client.aclose()

    r2 = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review", json={
        "action": "reject", "note": "no backup attached, please get the statement PDF"})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "unmatched"
    assert body["agreement_id"] is None
    assert body["agreement_number"] is None
    assert body["match_route"] is None
    assert body["legacy_settlement"] is False

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert fresh.consumed_amount == Decimal("0")


async def test_agreement_match_review_approve_keeps_link_and_consumed(admin_client, test_engine):
    """Mirror of the reject test above — the side that must NOT clean up.

    review_match()'s reject branch detaches agreement_id and releases
    consumed_amount. Nothing asserted the approve side leaves both standing, so
    an over-eager "clean up whenever the review ends" refactor would sail
    through: the invoice would come out matched but with no agreement link, its
    spend silently released from the NTE ceiling and its PA route gone. This
    test is that guard."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    delegate_client, delegate_id = await _delegate_client(test_engine)
    try:
        await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                json={"user_id": str(delegate_id)})
        r = await delegate_client.post(f"{INV_URL}/{inv['id']}/match", json={
            "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "match_review"
    finally:
        await delegate_client.aclose()

    r2 = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review", json={
        "action": "approve", "note": "statement checked against the paper slips"})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "matched"
    assert body["agreement_id"] == str(agr.id)
    assert body["agreement_number"] == agr.number
    assert body["match_route"] == "agreement"
    assert body["legacy_settlement"] is True
    assert body["legacy_settlement_reason"] == "backlog"

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert fresh.consumed_amount == Decimal("1000.00")


# ── The validity window is enforced by dates, not by a status ────────────────
#
# Nothing in this codebase ever writes "expired" or "closed": the only status
# write anywhere is approval-api engine._post_approve_agr setting "active",
# AgreementUpdate has no status field, and there is no scheduler. The tests
# above therefore only proved the window worked for a status that never
# occurs in production. These prove it for the status that DOES.

async def test_active_agreement_past_grace_is_not_a_candidate(admin_client, test_engine):
    """status still reads "active" (nothing ever changes it) but the window has
    closed — the candidate pool must refuse it anyway. Before the window was
    folded into the admission predicate this agreement was admissible forever,
    which is precisely the permanently-open PO this feature replaces."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id,
        status="active",
        valid_from=date.today() - timedelta(days=400),
        valid_to=date.today() - timedelta(days=60),
        grace_days=30,
    )
    inv = await _upload_invoice(admin_client, vendor_id)
    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(agr.id) not in ids


async def test_active_agreement_inside_grace_is_still_a_candidate(admin_client, test_engine):
    """The boundary the other way: past valid_to but inside grace_days stays in
    the pool, so the month's statement that lands after the period closes can
    still be matched (spec §5.1 / E7)."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id,
        status="active",
        valid_from=date.today() - timedelta(days=400),
        valid_to=date.today() - timedelta(days=5),
        grace_days=30,
    )
    inv = await _upload_invoice(admin_client, vendor_id)
    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(agr.id) in ids


async def test_match_refused_against_active_agreement_past_grace(admin_client, test_engine):
    """The candidate pool is a list; this is the enforcing gate. crud/invoice.py's
    agreement branch calls agreement_crud.is_admissible rather than restating
    the rule, so it must refuse the same row the pool omits."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id,
        status="active",
        valid_from=date.today() - timedelta(days=400),
        valid_to=date.today() - timedelta(days=60),
        grace_days=30,
    )
    inv = await _upload_invoice(admin_client, vendor_id, amount="500.00")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id),
        "legacy_settlement_reason": "backlog statement",
    })
    assert r.status_code == 422, r.text
    assert "not open for new spend" in r.text


# ── 1B: branch by agreement_type (Task 6) ───────────────────────────────────

async def test_recurring_match_does_not_flag_legacy_settlement(test_engine, admin_client):
    """1A 把**所有**协议匹配都标成无凭证付款 —— 那时协议匹配确实没有凭证。
    recurring 有排期行 + 履约确认,再统一标 legacy 会让协议详情页的
    'settled without receipt' 计数把每一张正常周期账单都算进去,那个健康度
    指标就废了。"""
    from app.crud import agreement_schedule as sched_crud
    from app.crud.invoice import match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        await sched_crud.ensure_period_rows(db, fresh_agr)
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        # 注意:没有传 legacy_settlement_reason —— recurring 不该再要求它。
        await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                         matched_by=user_id)
        await db.commit()

    async with factory() as db:
        done = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert done.legacy_settlement is False
        assert done.legacy_settlement_reason is None
        assert done.schedule_id is not None
        assert done.match_route_auto is True


async def test_house_account_match_still_requires_a_reason(test_engine, admin_client):
    """Task 5 narrows this, doesn't remove it: with no pickup slips selected,
    house_account is still the no-evidence settlement channel from 1A and a
    reason is still required. (The "slips selected" case is covered by
    test_slip_match.py — that's where legacy_settlement stops being forced.)"""
    from app.crud.invoice import AgreementMatchInvalid, match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="250.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(AgreementMatchInvalid, match="give a reason"):
            await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                             matched_by=user_id)


async def test_recurring_match_with_no_claimable_row_goes_to_match_review(test_engine, admin_client):
    """Out-of-tolerance is the routine case for usage-based billing, not an edge
    case — this is the path most real invoices take when a bill moves.
    claim_next_period returning None must not silently fall back to "matched":
    it has to land the invoice in match_review with no schedule row attached,
    driven through the real crud.invoice.match() entry point (not the crud
    helper directly), so the require_review reassignment inside
    _match_to_agreement is exercised end to end."""
    from app.crud import agreement_schedule as sched_crud
    from app.crud.invoice import match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    # Wildly outside [950, 1050] — every period row stays unclaimable.
    inv = await _upload_invoice(admin_client, vendor_id, amount="5000.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        await sched_crud.ensure_period_rows(db, fresh_agr)
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        result = await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                                  matched_by=user_id)
        await db.commit()

    assert result.status == "match_review"
    assert result.schedule_id is None
    assert result.match_route_auto is False
    assert result.legacy_settlement is False


# ── 1B: milestone route through the real match() entry point ───────────────
#
# All prior milestone coverage stopped at claim_milestone directly
# (test_agreement_schedule_claim.py). These drive the same scenarios through
# crud.invoice.match(), which is where the "missing schedule_id" guard and
# the ValueError → AgreementMatchInvalid translation actually live, and where
# match_route_auto's "recurring" guard has to hold on a genuine milestone
# success (the one case where claimed_row is not None but the type isn't
# recurring — the exact condition that guard exists to exclude).

async def test_milestone_match_requires_schedule_id(test_engine, admin_client):
    from app.crud.invoice import AgreementMatchInvalid, match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id,
                                       agreement_type="milestone")
    inv = await _upload_invoice(admin_client, vendor_id, amount="500.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(AgreementMatchInvalid, match="Pick the milestone stage"):
            await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                             matched_by=user_id)


async def test_milestone_match_surfaces_claim_error_as_agreement_match_invalid(
        test_engine, admin_client):
    """claim_milestone raises plain ValueError (unit-tested directly in
    test_agreement_schedule_claim.py); through match() that must surface as
    AgreementMatchInvalid, the exception type the HTTP layer maps to 422."""
    from app.crud.invoice import AgreementMatchInvalid, match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr_a = await _make_active_agreement(test_engine, vendor_id, user_id,
                                         agreement_type="milestone")
    agr_b = await _make_active_agreement(test_engine, vendor_id, user_id,
                                         agreement_type="milestone")
    inv = await _upload_invoice(admin_client, vendor_id, amount="500.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        foreign = AgreementPaymentSchedule(
            agreement_id=agr_b.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit", status="pending")
        db.add(foreign)
        await db.commit()
        await db.refresh(foreign)

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(AgreementMatchInvalid, match="does not belong"):
            await crud_match(db, db_inv, InvoiceMatchRequest(
                agreement_id=agr_a.id, schedule_id=foreign.id), matched_by=user_id)


async def test_milestone_match_succeeds_with_match_route_auto_false(test_engine, admin_client):
    """The only case where claimed_row is not None while agreement_type is not
    "recurring" — the exact condition
    `agr.agreement_type == "recurring" and claimed_row is not None` exists to
    exclude. If that guard is ever simplified to `claimed_row is not None`,
    this is the test that would catch it."""
    from app.crud.invoice import match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id,
                                       agreement_type="milestone")
    inv = await _upload_invoice(admin_client, vendor_id, amount="500.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit on signing", status="pending")
        db.add(row)
        await db.commit()
        await db.refresh(row)

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        result = await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, schedule_id=row.id), matched_by=user_id)
        await db.commit()

    assert result.status == "matched"
    assert result.match_route == "agreement"
    assert result.schedule_id == row.id
    assert result.match_route_auto is False
    assert result.legacy_settlement is False
    assert result.legacy_settlement_reason is None


# ── 1B: agreement→PO release must free the claimed schedule row ────────────
#
# Code review finding: the PO-route cleanup in match() already released
# agreement_id/consumed_amount (test_rematch_from_agreement_to_po_releases_
# consumption above) but left schedule_id and the claimed
# AgreementPaymentSchedule row untouched — a re-routed invoice would abandon
# a period permanently marked "received" against an invoice that no longer
# backs it, and no later invoice could ever claim that period again.

async def test_rematch_from_recurring_to_po_releases_schedule_row(admin_client, test_engine):
    from app.crud import agreement_schedule as sched_crud
    from app.crud.invoice import match as crud_match
    from app.schemas.invoice import AllocationInput, InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])
    inv_line_id = uuid.UUID(inv["line_items"][0]["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        await sched_crud.ensure_period_rows(db, fresh_agr)
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                         matched_by=user_id)
        await db.commit()

    async with factory() as db:
        claimed = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert claimed.schedule_id is not None
        claimed_schedule_id = claimed.schedule_id
        claimed_row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.id == claimed_schedule_id))).scalar_one()
        assert claimed_row.status == "received"
        assert claimed_row.invoice_id == inv_id

    po = (await admin_client.post("/api/v1/po", json={
        "title": "Recurring rematch PO", "type": 2, "vendor_id": str(vendor_id),
        "currency": "CAD", "tax_rate": "0.00",
        "line_items": [{"description": "Widget", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00"}],
    }))
    assert po.status_code in (200, 201), po.text
    po = po.json()
    po_line_id = uuid.UUID(po["line_items"][0]["id"])

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(allocations=[
            AllocationInput(invoice_line_id=inv_line_id, po_id=uuid.UUID(po["id"]),
                            po_line_id=po_line_id, allocated_amount=Decimal("1000.00"),
                            allocated_tax=Decimal("0.00")),
        ]), matched_by=user_id)
        await db.commit()

    async with factory() as db:
        fresh_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        released_row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.id == claimed_schedule_id))).scalar_one()

    assert fresh_inv.schedule_id is None
    assert fresh_inv.po_id == uuid.UUID(po["id"])
    assert released_row.status == "pending"
    assert released_row.invoice_id is None


# ── Fix round (Task 7 review, Important #3): review_match reject must also ──
# release a claimed schedule row — the sibling case to the rematch-to-PO
# release above. Before this fix, review_match()'s reject branch detached
# agreement_id/agreement_number/match_route but never touched schedule_id,
# so a delegate who genuinely claimed a real period (require_review is
# delegate-driven, independent of whether claim_next_period succeeded) and
# then got rejected would leave that period permanently "received" against
# an invoice that no longer backs it — no later invoice could ever claim it.

async def test_agreement_match_review_reject_releases_recurring_schedule_row(
        admin_client, test_engine):
    from app.crud import agreement_schedule as sched_crud
    from app.crud.invoice import match as crud_match
    from app.crud.invoice import review_match as crud_review_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        await sched_crud.ensure_period_rows(db, fresh_agr)
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        # require_review=True with an in-tolerance amount: the period IS
        # claimed, but the match still lands in match_review (delegate-shaped
        # call, mirroring test_agreement_match_by_delegate_requires_review).
        matched = await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                                   matched_by=user_id, require_review=True)
        await db.commit()
    assert matched.status == "match_review"
    assert matched.schedule_id is not None
    claimed_schedule_id = matched.schedule_id

    async with factory() as db:
        claimed_row_before = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.id == claimed_schedule_id))).scalar_one()
    assert claimed_row_before.status == "received"
    assert claimed_row_before.invoice_id == inv_id

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        rejected = await crud_review_match(db, db_inv, "reject", "wrong statement", user_id)
        await db.commit()
    assert rejected.schedule_id is None
    assert rejected.status == "unmatched"

    async with factory() as db:
        released_row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.id == claimed_schedule_id))).scalar_one()
    assert released_row.status == "pending"
    assert released_row.invoice_id is None


# ── Whole-branch review Blocker 2: the manual-assignment escape hatch ──────
#
# Spec §4.3 step 5: an invoice claim_next_period can't place (out of
# tolerance / no candidate row) is meant to be resolvable by a human
# explicitly picking the period — "由人工指定期次". Before this fix that
# escape hatch did not exist: req.schedule_id was read on the milestone
# branch only, never honoured on recurring, so an out-of-tolerance invoice
# landed in match_review with schedule_id NULL and had NO way forward —
# approving there never sets schedule_id (see
# test_recurring_pa_refused_when_invoice_never_claimed_a_period in
# test_agreement_pa.py), and rejecting-then-rematching without an explicit
# override just reruns the same FIFO row against the same tolerance forever.

async def test_recurring_match_honours_explicit_schedule_id_out_of_tolerance(
        test_engine, admin_client):
    """A human overriding FIFO/tolerance on purpose: an explicit schedule_id
    claims that exact row, skips the amount check entirely, and — unlike a
    genuine FIFO auto-claim — is reported as NOT automatic."""
    from app.crud import agreement_schedule as sched_crud
    from app.crud.invoice import match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Manual Assign Vendor")
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    # $1,180 vs $1,000 ±5% ([950, 1050]) — wildly out of tolerance.
    inv = await _upload_invoice(admin_client, vendor_id, amount="1180.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        await sched_crud.ensure_period_rows(db, fresh_agr)
        target_row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id,
            AgreementPaymentSchedule.schedule_type == "period",
        ).order_by(AgreementPaymentSchedule.sequence).limit(1))).scalar_one()
        await db.commit()
    target_row_id = target_row.id

    # First, confirm the "stuck forever" failure mode: FIFO alone can never
    # place this invoice.
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        stuck = await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                                 matched_by=user_id)
        await db.commit()
    assert stuck.status == "match_review"
    assert stuck.schedule_id is None

    # AP rejects (back to "unmatched" — a no-op release since schedule_id was
    # already NULL), then re-matches with an EXPLICIT schedule_id: the
    # manual-assignment override.
    r_reject = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review", json={
        "action": "reject", "note": "manually assigning the period instead"})
    assert r_reject.status_code == 200, r_reject.text
    assert r_reject.json()["status"] == "unmatched"

    r_match = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "schedule_id": str(target_row_id)})
    assert r_match.status_code == 200, r_match.text
    body = r_match.json()
    assert body["status"] == "matched"          # admin_client is AP → require_review=False
    assert body["match_route_auto"] is False    # a human overrode FIFO — not "automatic"

    # InvoiceResponse does not expose schedule_id — check the row directly.
    async with factory() as db:
        db_inv_after = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert db_inv_after.schedule_id == target_row_id
        claimed_row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.id == target_row_id))).scalar_one()
    assert claimed_row.status == "received"
    assert claimed_row.invoice_id == inv_id


async def test_recurring_match_rejects_an_explicit_schedule_id_from_another_agreement(
        test_engine, admin_client):
    """The override is not a blank check — claim_specific_period must still
    refuse a row that does not belong to THIS agreement, the same way
    claim_milestone already refuses a foreign milestone row."""
    from app.crud import agreement_schedule as sched_crud
    from app.crud.invoice import AgreementMatchInvalid, match as crud_match
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Manual Assign Cross Vendor")
    agr_a = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    agr_b = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    inv = await _upload_invoice(admin_client, vendor_id, amount="1180.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh_b = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr_b.id))).scalar_one()
        await sched_crud.ensure_period_rows(db, fresh_b)
        foreign_row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr_b.id,
        ).limit(1))).scalar_one()
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(AgreementMatchInvalid, match="does not belong"):
            await crud_match(db, db_inv, InvoiceMatchRequest(
                agreement_id=agr_a.id, schedule_id=foreign_row.id), matched_by=user_id)


# ── Whole-branch review Blocker 4 (4a/4b): release must also clear the ─────
# confirmation stamp and complete the open task ─────────────────────────────
#
# _release_schedule_row used to reset only status/invoice_id. 4a: a
# delegate's match claims a period and gets it confirmed; AP then rejects the
# match — the row released back to "pending" but STILL stamped confirmed, so
# a later invoice re-claiming that period sails past the PA gate
# (accepted_at IS NULL) with nobody having confirmed ITS cycle. 4b: release
# never completed the open confirm_period task, so the re-claim's
# create_confirm_task added a SECOND task with the same document_number —
# agreements.py's confirm endpoint used .scalar_one_or_none() there, which
# raises MultipleResultsFound (a 500) the instant that happens.

async def test_release_then_reclaim_clears_confirmation_and_leaves_one_open_task(
        admin_client, test_engine):
    from app.crud import agreement_schedule as sched_crud
    from app.crud.invoice import match as crud_match
    from app.crud.invoice import review_match as crud_review_match
    from app.models.task import Task
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Release Reclaim Vendor")
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        fresh_agr.owner_id = user_id
        await sched_crud.ensure_period_rows(db, fresh_agr)
        await db.commit()

    inv_x = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_x_id = uuid.UUID(inv_x["id"])

    # Delegate-shaped match (require_review=True, mirrors
    # test_agreement_match_review_reject_releases_recurring_schedule_row's
    # setup) claims period 1 and creates a confirm task.
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_x_id))).scalar_one()
        matched_x = await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                                     matched_by=user_id, require_review=True)
        await db.commit()
    assert matched_x.status == "match_review"
    schedule_id = matched_x.schedule_id
    assert schedule_id is not None

    # The owner confirms it — a REAL confirmation, not an unclaimed row.
    async with factory() as db:
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        confirmed_row = await sched_crud.confirm_period(db, fresh_agr, schedule_id, user_id)
        await db.commit()
    assert confirmed_row.accepted_at is not None

    # AP rejects the match — the whole point of this test: releasing a REAL,
    # ALREADY-CONFIRMED period, not an unconfirmed one.
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_x_id))).scalar_one()
        rejected = await crud_review_match(db, db_inv, "reject", "wrong statement", user_id)
        await db.commit()
    assert rejected.schedule_id is None

    async with factory() as db:
        released_row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.id == schedule_id))).scalar_one()
        open_tasks = (await db.execute(select(Task).where(
            Task.document_type == "agr", Task.document_id == agr.id,
            Task.type == "confirm_period",
            Task.document_number == f"{agr.number} · {released_row.period_label}",
            Task.is_completed.is_(False),
        ))).scalars().all()
    # 4a: the confirmation stamp must NOT survive the release.
    assert released_row.status == "pending"
    assert released_row.invoice_id is None
    assert released_row.accepted_by is None
    assert released_row.accepted_at is None
    # 4b: the original confirm_period task must be completed, not left open.
    assert open_tasks == []

    # Invoice Y re-claims the SAME period (FIFO picks the lowest pending
    # sequence, which is period 1 again now that it's back to "pending").
    inv_y = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_y_id = uuid.UUID(inv_y["id"])
    async with factory() as db:
        db_inv_y = (await db.execute(select(Invoice).where(Invoice.id == inv_y_id))).scalar_one()
        matched_y = await crud_match(db, db_inv_y, InvoiceMatchRequest(agreement_id=agr.id),
                                     matched_by=user_id)
        await db.commit()
    assert matched_y.schedule_id == schedule_id   # the very same row

    async with factory() as db:
        reclaimed_row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.id == schedule_id))).scalar_one()
        open_tasks_after = (await db.execute(select(Task).where(
            Task.document_type == "agr", Task.document_id == agr.id,
            Task.type == "confirm_period",
            Task.document_number == f"{agr.number} · {reclaimed_row.period_label}",
            Task.is_completed.is_(False),
        ))).scalars().all()
    # Nobody has confirmed service for invoice Y's cycle — without 4a this
    # would still read as confirmed, carried over from invoice X.
    assert reclaimed_row.accepted_at is None
    # Exactly one open task — without 4b this would be two (the never-closed
    # original plus the new one from this re-claim's create_confirm_task).
    assert len(open_tasks_after) == 1

    # The actual HTTP path Blocker 4b's 500 (MultipleResultsFound) was
    # reachable through must not blow up.
    r_confirm = await admin_client.post(
        f"/api/v1/agreements/{agr.id}/schedule/{schedule_id}/confirm")
    assert r_confirm.status_code == 200, r_confirm.text


# ── Whole-branch review Item 9: Data-Maintenance invoice delete must ───────
# release a claimed schedule row too — agreement_payment_schedule.invoice_id
# has no FK back to invoices (a shared table), so hard-deleting the invoice
# straight out from under a claimed row used to strand it "received" and
# pointing at a ghost: never re-claimable (status never returns to
# "pending"/"overdue") and never swept (the overdue sweep only touches
# "pending" rows).

async def test_data_maintenance_invoice_delete_releases_the_schedule_row(
        admin_client, test_engine):
    from app.admin import service as admin_service
    from app.crud import agreement_schedule as sched_crud
    from app.crud.invoice import match as crud_match
    from app.models.invoice import Invoice
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(
        test_engine, vendor_name="DM Delete Vendor")
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh_agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        await sched_crud.ensure_period_rows(db, fresh_agr)
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        matched = await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                                   matched_by=user_id)
        await db.commit()
    schedule_id = matched.schedule_id
    assert schedule_id is not None

    async with factory() as db:
        await admin_service.delete_record(
            db, "invoice", inv_id, actor_id=user_id, actor_email="admin@example.com")
        await db.commit()

    async with factory() as db:
        cnt = (await db.execute(select(func.count()).select_from(Invoice).where(
            Invoice.id == inv_id))).scalar_one()
        assert cnt == 0
        released_row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.id == schedule_id))).scalar_one()
    assert released_row.status == "pending"
    assert released_row.invoice_id is None


# ── Task 4: _release_schedule_row → _release_agreement_evidence must also ──
# free claimed slips at BOTH call sites, not just the schedule-row half.
# test_slip_release.py proves the helper itself is correct in isolation;
# these two drive the REAL entry points (match()'s agreement→PO switch,
# review_match()'s reject path) because a unit test on the helper says
# nothing about whether the call sites still call it — this project has
# already shipped a rename that silently dropped a caller once
# (_release_schedule_row's own history, see its docstring). Slip writing
# itself (invoice.slip_ids / slip_variance_reason) is Task 5/6 work and not
# built yet, so these tests seed the claim directly on the DB rows — the
# same shape review will produce once it exists — rather than going through
# an unimplemented request field.

async def test_route_switch_to_po_releases_claimed_slips(admin_client, test_engine):
    """Mirrors test_rematch_from_agreement_to_po_releases_consumption, but for
    a house_account invoice holding THREE slips instead of a recurring
    schedule row. A release loop that frees only the first slip would pass a
    single-slip test and still leave the agreement under-reporting what's
    owed — so this asserts all three."""
    from app.crud.invoice import match as crud_match
    from app.models.agreement_slip import AgreementPickupSlip
    from app.schemas.invoice import AllocationInput, InvoiceMatchRequest

    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Slip Route Switch Vendor")
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])
    inv_line_id = uuid.UUID(inv["line_items"][0]["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, legacy_settlement_reason="statement"), matched_by=user_id)

    # Seed three slips claimed by this invoice — the shape Task 5/6's match
    # will produce, written directly since that path doesn't exist yet.
    async with factory() as db:
        slips = [
            AgreementPickupSlip(
                agreement_id=agr.id, slip_date=date(2026, 7, 15), slip_ref=f"RS-{i}",
                amount=Decimal("300.00"), tax_amount=Decimal("0"), total_amount=Decimal("300.00"),
                picked_by=user_id, created_by=user_id, status="reconciled", invoice_id=inv_id)
            for i in range(3)
        ]
        db.add_all(slips)
        await db.flush()
        slip_ids = [s.id for s in slips]
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        db_inv.slip_ids = [str(s) for s in slip_ids]
        db_inv.slip_variance_reason = "rounding"
        await db.commit()

    po = (await admin_client.post("/api/v1/po", json={
        "title": "Slip route switch PO", "type": 2, "vendor_id": str(vendor_id),
        "currency": "CAD", "tax_rate": "0.00",
        "line_items": [{"description": "Widget", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00"}],
    }))
    assert po.status_code in (200, 201), po.text
    po = po.json()
    po_line_id = uuid.UUID(po["line_items"][0]["id"])

    # Move the invoice to the PO route — the invoice no longer backs the
    # slips it claimed.
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(allocations=[
            AllocationInput(invoice_line_id=inv_line_id, po_id=uuid.UUID(po["id"]),
                            po_line_id=po_line_id, allocated_amount=Decimal("1000.00"),
                            allocated_tax=Decimal("0.00")),
        ]), matched_by=user_id)
        await db.commit()

    async with factory() as db:
        fresh_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        released_rows = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id.in_(slip_ids)))).scalars().all()

    assert fresh_inv.match_route == "po"
    assert fresh_inv.slip_ids is None
    assert fresh_inv.slip_variance_reason is None
    assert len(released_rows) == 3
    assert all(r.status == "open" for r in released_rows)
    assert all(r.invoice_id is None for r in released_rows)


async def test_match_review_reject_releases_claimed_slips(admin_client, test_engine):
    """Mirrors test_agreement_match_review_reject_releases_recurring_schedule_row
    for the slip side: a delegate-shaped house_account match lands in
    match_review holding claimed slips, AP rejects it, and every slip must
    come back — not just the first one."""
    from app.crud.invoice import match as crud_match
    from app.crud.invoice import review_match as crud_review_match
    from app.models.agreement_slip import AgreementPickupSlip
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _name, user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Slip Reject Vendor")
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        matched = await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, legacy_settlement_reason="statement"),
            matched_by=user_id, require_review=True)
        await db.commit()
    assert matched.status == "match_review"

    async with factory() as db:
        slips = [
            AgreementPickupSlip(
                agreement_id=agr.id, slip_date=date(2026, 7, 15), slip_ref=f"RJ-{i}",
                amount=Decimal("300.00"), tax_amount=Decimal("0"), total_amount=Decimal("300.00"),
                picked_by=user_id, created_by=user_id, status="reconciled", invoice_id=inv_id)
            for i in range(3)
        ]
        db.add_all(slips)
        await db.flush()
        slip_ids = [s.id for s in slips]
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        db_inv.slip_ids = [str(s) for s in slip_ids]
        db_inv.slip_variance_reason = "rounding"
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        rejected = await crud_review_match(db, db_inv, "reject", "wrong statement", user_id)
        await db.commit()
    assert rejected.status == "unmatched"
    assert rejected.slip_ids is None
    assert rejected.slip_variance_reason is None

    async with factory() as db:
        released_rows = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id.in_(slip_ids)))).scalars().all()
    assert len(released_rows) == 3
    assert all(r.status == "open" for r in released_rows)
    assert all(r.invoice_id is None for r in released_rows)
