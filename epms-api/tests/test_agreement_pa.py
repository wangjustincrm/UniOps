"""PA created from an agreement-matched invoice (no PO, no GR)."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.agreement import PurchaseAgreement
from app.models.department import Department
from app.schemas.auth import RegisterRequest
from httpx import ASGITransport, AsyncClient
from tests.test_agreement_invoice_match import _make_active_agreement, _upload_invoice
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio

PA_URL = "/api/v1/pa"


@pytest.fixture(autouse=True)
async def _ensure_approval_dept_routing_table(test_engine):
    """_effective_role_codes queries approval_dept_routing unconditionally
    (director derivation) on every build_scope() call — which now includes
    every PA list/detail call via visible_agreement_subquery (fix round,
    I-1). That table is approval-api-owned (same physical DB in prod) and
    only exists in this standalone epms_test DB if some earlier test file
    already created it (mirrors test_access_scope_dept.py's own fixture).
    Running this file in isolation, as the fix-round instructions do, needs
    it created here too rather than relying on file-run-order leftovers."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " gm_or_opm varchar(3) NOT NULL DEFAULT 'gm',"
            " director_user_id uuid NULL,"
            " supervisor_enabled boolean NOT NULL DEFAULT false,"
            " updated_by uuid NULL,"
            " updated_at timestamptz NOT NULL DEFAULT now()"
            ")"
        ))
        await db.commit()
    yield


async def _make_department(test_engine, name: str) -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        dept = Department(code=f"D{uuid.uuid4().hex[:6].upper()}", name=name, is_active=True)
        db.add(dept)
        await db.commit()
        await db.refresh(dept)
        return dept.id


async def _client_with_user(test_engine, role: str, department_id: uuid.UUID | None = None):
    """A fresh authenticated client PLUS the user id behind it (conftest's
    admin_client/requester_client only give you the client)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name=f"Scope {role}", role=role, department_id=department_id,
        ))
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    app = create_app()
    client = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return client, user.id


async def _match_and_pay(admin_client, vendor_id, agr, amount: str) -> dict:
    """Match a fresh invoice to `agr` and raise a regular agreement PA for it,
    via admin_client (system_admin) — keeps PaymentApplication.created_by
    distinct from any scoped test user, so scoping tests exercise the
    agreement-ownership branch and not the PA.created_by shortcut."""
    inv = await _upload_invoice(admin_client, vendor_id, amount=amount)
    m = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    assert m.status_code == 200, m.text
    r = await admin_client.post(PA_URL, json={
        "title": f"Statement {agr.number}", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": amount, "tax_amount": "0.00", "payment_amount": amount,
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": amount, "line_total": amount}],
    })
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
async def agreement_matched_invoice(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    assert r.status_code == 200, r.text
    return inv, agr


@pytest.fixture
async def unmatched_invoice(admin_client, test_engine):
    vendor_id, _vendor_name, _user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Unmatched Vendor")
    return await _upload_invoice(admin_client, vendor_id, amount="50.00")


async def test_create_pa_from_agreement(admin_client, agreement_matched_invoice):
    inv, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "Princess Auto July statement",
        "agreement_id": str(agr.id),
        "invoice_ids": [inv["id"]],
        "subtotal": "1000.00",
        "tax_amount": "130.00",
        "payment_amount": "1130.00",
        "line_items": [{"description": "July statement", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["agreement_id"] == str(agr.id)
    assert body["agreement_number"] == agr.number
    assert body["po_id"] is None
    assert body["status"] == "draft"


async def test_agreement_pa_skips_the_goods_receipt_gate(admin_client, agreement_matched_invoice):
    """No GR exists and none ever will for this route — the gate must not fire,
    and no receipt_override reason should be demanded."""
    inv, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "No GR here",
        "agreement_id": str(agr.id),
        "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r.status_code == 201, r.text
    assert r.json()["receipt_override"] is False


async def test_agreement_pa_appears_in_the_epms_list(admin_client, agreement_matched_invoice):
    """crud.pa.get_all filters on po_id IS NOT NULL to keep OA's Direct PAs out;
    that filter must not also hide agreement PAs."""
    inv, agr = agreement_matched_invoice
    created = (await admin_client.post(PA_URL, json={
        "title": "Listed?", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })).json()

    listed = await admin_client.get(PA_URL)
    assert listed.status_code == 200
    assert created["id"] in [i["id"] for i in listed.json()["items"]]


async def test_pa_requires_exactly_one_source(admin_client, agreement_matched_invoice):
    inv, agr = agreement_matched_invoice
    neither = await admin_client.post(PA_URL, json={
        "title": "Neither", "invoice_ids": [inv["id"]],
        "subtotal": "1.00", "tax_amount": "0.00", "payment_amount": "1.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1.00", "line_total": "1.00"}],
    })
    assert neither.status_code == 422
    assert "po_id" in neither.text or "agreement_id" in neither.text


async def test_invoice_not_on_agreement_is_refused(admin_client, agreement_matched_invoice,
                                                   unmatched_invoice):
    _, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "Wrong invoice", "agreement_id": str(agr.id),
        "invoice_ids": [unmatched_invoice["id"]],
        "subtotal": "1.00", "tax_amount": "0.00", "payment_amount": "1.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1.00", "line_total": "1.00"}],
    })
    assert r.status_code == 422


# ── Fix round: C-1 — agreement PAs must be openable/actionable, not just listable ──

async def test_get_agreement_pa_is_not_404d_by_the_po_only_guard(admin_client, agreement_matched_invoice):
    """Before the fix, every individual-record endpoint guarded on
    `pa.po_id is None` alone to mean 'OA Direct PA, 404 it' — an agreement PA
    also has po_id NULL, so it 404d too. Only `po_id IS NULL AND agreement_id
    IS NULL` should mean Direct PA."""
    inv, agr = agreement_matched_invoice
    created = (await admin_client.post(PA_URL, json={
        "title": "Openable?", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })).json()

    r = await admin_client.get(f"{PA_URL}/{created['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == created["id"]


async def test_agreement_pa_action_reaches_the_approval_engine(admin_client, agreement_matched_invoice):
    """POST /pa/{id}/action is the ONLY write path to submit/approve/process a
    PA. Before the fix it 404d before ever calling delegate_action, which
    permanently stuck every agreement PA in draft — the phase-goal-defeating
    bug (C-1). approval-api isn't reachable in this test env, so delegate_action
    is mocked; the assertion that matters is that it gets CALLED at all."""
    inv, agr = agreement_matched_invoice
    created = (await admin_client.post(PA_URL, json={
        "title": "Actionable?", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })).json()

    with patch("app.api.v1.pa.delegate_action", new_callable=AsyncMock) as mock_delegate:
        mock_delegate.return_value = {"status": "submitted", "message": "OK"}
        r = await admin_client.post(f"{PA_URL}/{created['id']}/action", json={"action": "submit"})
    assert r.status_code == 200, r.text
    mock_delegate.assert_called_once()


# ── Fix round: I-2 — agreement route only accepts pa_type='regular' ──────────────

async def test_agreement_pa_rejects_non_regular_pa_type(admin_client, agreement_matched_invoice):
    """No PO on this route means none of the prepayment/settlement/balance
    guards (vendor cap, prepayment-PA ownership, applied <= prepaid) ever run
    — routing a settlement through an agreement would skip all of them."""
    inv, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "Settlement via agreement?", "agreement_id": str(agr.id),
        "pa_type": "settlement", "prepayment_pa_id": str(uuid.uuid4()),
        "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r.status_code == 422, r.text


# ── Fix round: I-3 — agreement admissibility + invoice evidence required ─────────

async def test_agreement_pa_refused_when_agreement_not_admissible(admin_client, agreement_matched_invoice,
                                                                    test_engine):
    """The agreement itself is this route's entire authorisation (no GR, no
    override) — a PA cannot be raised against one that was never approved (or
    has fallen past its post-expiry grace window), even with a validly
    matched invoice in hand."""
    inv, agr = agreement_matched_invoice
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_agr = (await db.execute(
            select(PurchaseAgreement).where(PurchaseAgreement.id == agr.id))).scalar_one()
        db_agr.status = "cancelled"
        await db.commit()

    r = await admin_client.post(PA_URL, json={
        "title": "Cancelled agreement", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r.status_code == 422, r.text


async def test_agreement_pa_requires_at_least_one_invoice(admin_client, agreement_matched_invoice):
    """There is no receipt gate/override on this route — the linked invoice(s)
    are the only evidence a PA pays real, already-billed spend rather than an
    arbitrary amount against an approved ceiling."""
    inv, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "No invoices", "agreement_id": str(agr.id), "invoice_ids": [],
        "subtotal": "1.00", "tax_amount": "0.00", "payment_amount": "1.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1.00", "line_total": "1.00"}],
    })
    assert r.status_code == 422, r.text


# ── Fix round: I-1 — agreement PA visibility must be scoped, not unconditional ───

async def test_agreement_pa_department_scope_excludes_other_departments(admin_client, test_engine):
    """The code review's core finding: before this fix, a restricted
    (non-admin) caller's PA list unconditionally admitted every agreement PA
    regardless of department, because the widened list filter bypassed
    po_ids_subq scoping entirely for agreement_id IS NOT NULL rows. A
    dept_manager scoped to Dept A must see Dept A's house-account statement
    and NOT Dept B's — same for the individual GET."""
    dept_a = await _make_department(test_engine, "Dept Scope A")
    dept_b = await _make_department(test_engine, "Dept Scope B")
    vendor_id, _vn, user_id = await seed_vendor_and_user(test_engine, vendor_name="Dept Scope Vendor")
    agr_a = await _make_active_agreement(test_engine, vendor_id, user_id, department_id=dept_a)
    agr_b = await _make_active_agreement(test_engine, vendor_id, user_id, department_id=dept_b)

    pa_a = await _match_and_pay(admin_client, vendor_id, agr_a, "100.00")
    pa_b = await _match_and_pay(admin_client, vendor_id, agr_b, "200.00")

    client_a, _uid = await _client_with_user(test_engine, "dept_manager", department_id=dept_a)
    try:
        listed = await client_a.get(PA_URL)
        assert listed.status_code == 200, listed.text
        ids = [i["id"] for i in listed.json()["items"]]
        assert pa_a["id"] in ids
        assert pa_b["id"] not in ids

        assert (await client_a.get(f"{PA_URL}/{pa_a['id']}")).status_code == 200
        assert (await client_a.get(f"{PA_URL}/{pa_b['id']}")).status_code == 404
    finally:
        await client_a.aclose()


async def test_agreement_pa_requester_sees_only_agreements_they_own(admin_client, test_engine):
    """A plain requester's list must show an agreement PA only when the
    requester created (or is the named owner of) the AGREEMENT — the code
    review named this exact role: requester defaults to view_pa=True via the
    matrix's _VIEW_ALL, so an unscoped OR would leak every house-account
    statement company-wide to every requester."""
    vendor_id, _vn, _creator_id = await seed_vendor_and_user(
        test_engine, vendor_name="Requester Scope Vendor")
    owner_client, owner_id = await _client_with_user(test_engine, "requester")
    other_client, _other_id = await _client_with_user(test_engine, "requester")
    try:
        agr = await _make_active_agreement(test_engine, vendor_id, owner_id)
        pa = await _match_and_pay(admin_client, vendor_id, agr, "50.00")

        owner_listed = await owner_client.get(PA_URL)
        other_listed = await other_client.get(PA_URL)
        assert pa["id"] in [i["id"] for i in owner_listed.json()["items"]]
        assert pa["id"] not in [i["id"] for i in other_listed.json()["items"]]
    finally:
        await owner_client.aclose()
        await other_client.aclose()


# ── Fix round: M-1 / M-2 — department filter + search reach agreement PAs ────────

async def test_pa_department_filter_matches_agreement_department(admin_client, test_engine):
    from app.crud import pa as pa_crud

    dept = await _make_department(test_engine, "Filter Dept")
    other_dept = await _make_department(test_engine, "Other Filter Dept")
    vendor_id, _vn, user_id = await seed_vendor_and_user(test_engine, vendor_name="Dept Filter Vendor")
    agr = await _make_active_agreement(test_engine, vendor_id, user_id, department_id=dept)
    pa = await _match_and_pay(admin_client, vendor_id, agr, "75.00")

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        matched, _ = await pa_crud.get_all(db, department_id=dept)
        unmatched, _ = await pa_crud.get_all(db, department_id=other_dept)
    assert pa["id"] in [str(p.id) for p in matched]
    assert pa["id"] not in [str(p.id) for p in unmatched]


async def test_pa_search_matches_agreement_number(admin_client, test_engine):
    vendor_id, _vn, user_id = await seed_vendor_and_user(test_engine, vendor_name="Search Vendor")
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    pa = await _match_and_pay(admin_client, vendor_id, agr, "60.00")

    r = await admin_client.get(PA_URL, params={"search": agr.number})
    assert r.status_code == 200, r.text
    assert pa["id"] in [i["id"] for i in r.json()["items"]]


# ── Fix round 2: NEW-2 — task-chain visibility for out-of-department approvers ───

async def test_agreement_pa_task_chain_visibility_across_departments(admin_client, test_engine):
    """approval-api's dept_manager/gm_or_opm/director approval step for an
    agreement PA routes on the PA CREATOR's own department
    (_routing_department_id has no agreement case, falls through to
    doc.created_by's department) — NOT the agreement's own department_id that
    visible_agreement_subquery scopes list visibility on. Those two can
    diverge, so a dept_manager assigned the approve_pa task for an agreement
    PA in a DIFFERENT department must still find it in their list via the
    task-chain fallback — exactly how PO-based PAs already work
    (_task_chain_po_ids). Without it they'd get the task but the PA would be
    invisible everywhere except the direct-task shortcut on GET /pa/{id}."""
    from app.models.task import Task

    agreement_dept = await _make_department(test_engine, "Agreement Home Dept")
    approver_dept = await _make_department(test_engine, "Approver's Own Dept")
    vendor_id, _vn, user_id = await seed_vendor_and_user(test_engine, vendor_name="Task Chain Vendor")
    agr = await _make_active_agreement(test_engine, vendor_id, user_id, department_id=agreement_dept)
    pa = await _match_and_pay(admin_client, vendor_id, agr, "90.00")

    approver_client, approver_id = await _client_with_user(
        test_engine, "dept_manager", department_id=approver_dept)
    try:
        # Sanity: WITHOUT a task, an out-of-department dept_manager does not see it.
        before = await approver_client.get(PA_URL)
        assert pa["id"] not in [i["id"] for i in before.json()["items"]]

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add(Task(
                type="approve_pa", priority="normal", document_type="pa",
                document_id=uuid.UUID(pa["id"]), document_number=pa["pa_number"],
                assigned_role="dept_manager", assigned_user_id=approver_id,
                title="Approve PA", description="test task",
            ))
            await db.commit()

        after = await approver_client.get(PA_URL)
        assert pa["id"] in [i["id"] for i in after.json()["items"]]
        assert (await approver_client.get(f"{PA_URL}/{pa['id']}")).status_code == 200
    finally:
        await approver_client.aclose()


# ── Fix round 2: S-4 — the linked invoice must have CLEARED matching ──────────

async def test_agreement_pa_refused_for_invoice_still_in_match_review(admin_client, test_engine):
    """The agreement route has no GR, so the invoice is the ONLY evidence — and
    an invoice sitting at "match_review" has not been reviewed yet. Its
    agreement_id is already set at that point, so the "matched to THIS
    agreement" check alone lets a PA be raised before the reviewer answers,
    quietly bypassing the review gate added in ebeb3f5."""
    from tests.test_agreement_invoice_match import _delegate_client

    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")

    delegate_client, delegate_id = await _delegate_client(test_engine)
    try:
        await admin_client.post(f"/api/v1/invoices/{inv['id']}/assign-match",
                                json={"user_id": str(delegate_id)})
        r = await delegate_client.post(f"/api/v1/invoices/{inv['id']}/match", json={
            "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "match_review"
    finally:
        await delegate_client.aclose()

    r = await admin_client.post(PA_URL, json={
        "title": "Paying an unreviewed statement", "agreement_id": str(agr.id),
        "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r.status_code == 422, r.text
    assert "not cleared for payment" in r.text

    # …and once the review is approved the same request goes through, proving
    # the gate is the review state and not something incidental.
    r2 = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match-review",
                                 json={"action": "approve", "note": "ok"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "matched"

    r3 = await admin_client.post(PA_URL, json={
        "title": "Paying a reviewed statement", "agreement_id": str(agr.id),
        "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r3.status_code == 201, r3.text


# ── Fix round 3 (Task 7 review, Critical): the confirmation gate's INNER JOIN ──
# let an unlinked recurring invoice straight through. The join
# (Invoice.schedule_id == AgreementPaymentSchedule.id) produces zero rows —
# and therefore no complaint — for any invoice whose schedule_id is NULL.
# That state is reachable via the routine out-of-tolerance path: claim_next_
# period returns None, the invoice lands in match_review with schedule_id
# left NULL, and an AP reviewer's plain approve() there sets status="matched"
# unconditionally, never touching schedule_id. The gate must reject that
# invoice directly (checked against the already-fetched `rows`, not a join
# that can only see invoices already linked to a row).

async def test_recurring_pa_refused_when_invoice_never_claimed_a_period(admin_client, test_engine):
    from decimal import Decimal

    from app.crud import agreement_schedule as sched_crud
    from app.crud.invoice import match as crud_match
    from app.models.invoice import Invoice
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _vn, user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Gate Bypass Vendor")
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"))
    # Wildly outside [950, 1050] — no period row is ever claimable.
    inv = await _upload_invoice(admin_client, vendor_id, amount="9999.00")
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
    assert matched.status == "match_review"
    assert matched.schedule_id is None

    # AP reviewer approves — status flips to "matched" but schedule_id stays
    # NULL forever: review_match()'s approve branch never touches it.
    r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match-review", json={
        "action": "approve", "note": "checked against the vendor portal"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"

    pa = await admin_client.post(PA_URL, json={
        "title": "Should be refused", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "9999.00", "tax_amount": "0.00", "payment_amount": "9999.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "9999.00", "line_total": "9999.00"}],
    })
    assert pa.status_code == 422, pa.text
    assert "not linked to a billing period" in pa.text


async def test_house_account_pa_not_gated_by_schedule_id(admin_client, agreement_matched_invoice):
    """house_account invoices never get a schedule_id — ensure_period_rows only
    ever runs for agreement_type='recurring'. The new recurring-only gate must
    not reach for schedule_id on this route at all, or every house_account PA
    (the entire pre-existing suite above) would start failing."""
    inv, agr = agreement_matched_invoice
    assert agr.agreement_type == "house_account"
    r = await admin_client.post(PA_URL, json={
        "title": "House account statement", "agreement_id": str(agr.id),
        "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r.status_code == 201, r.text


async def test_milestone_pa_not_gated_by_confirmation(admin_client, test_engine):
    """milestone has no acceptance step in this phase (design §5.3) — a
    milestone-matched invoice (schedule_id IS set, to the milestone row, with
    accepted_at always NULL in 1B) must still be payable; the recurring-only
    gate must not reach it."""
    from app.crud.invoice import match as crud_match
    from app.models.agreement_schedule import AgreementPaymentSchedule
    from app.models.invoice import Invoice
    from app.schemas.invoice import InvoiceMatchRequest

    vendor_id, _vn, user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Milestone Gate Vendor")
    agr = await _make_active_agreement(test_engine, vendor_id, user_id,
                                       agreement_type="milestone")
    inv = await _upload_invoice(admin_client, vendor_id, amount="500.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit", status="pending")
        db.add(row)
        await db.commit()
        await db.refresh(row)

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, schedule_id=row.id), matched_by=user_id)
        await db.commit()

    pa = await admin_client.post(PA_URL, json={
        "title": "Milestone payment", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "500.00", "tax_amount": "0.00", "payment_amount": "500.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "500.00", "line_total": "500.00"}],
    })
    assert pa.status_code == 201, pa.text
