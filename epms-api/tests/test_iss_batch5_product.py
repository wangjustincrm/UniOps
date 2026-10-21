"""Batch 5 — EPMS product fixes outside the Assistant.

ISS-0077  History showed an engine-skipped step as "Approve by <whoever
          triggered the skip>". Events now carry a derived `kind`.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.approval_event_kind import (
    MACHINE_APPROVAL_MARKERS, event_kind, is_machine_approval, skip_reason,
)
from app.core.config import settings
from app.models.approval import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor

# The comments approval-api's engine writes (app/crud/engine.py: _step_skip,
# _ob_step_waived, the same-approver loop and the config re-sync), copied
# verbatim. If the engine's wording changes, these must change with it.
ENGINE_SKIP_NO_DIRECTOR = "Auto-skipped (department has no Director)"
ENGINE_SKIP_DIRECTOR_INACTIVE = "Auto-skipped ⚠ configured Director is inactive/missing"
ENGINE_RESYNC = ("Re-synced (config change): Auto-skipped (over-budget approval no longer "
                 "required — Budget Config mode changed to Allow without extra approval)")
ENGINE_SAME_APPROVER = "Auto-approved (same approver holds both roles)"
PMS_IMPORT = "Auto-approved on behalf of Finance Manager — PMS migration (PA-1)"


def _uid(client) -> uuid.UUID:
    token = client.headers["Authorization"].split()[1]
    return uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                                algorithms=[settings.JWT_ALGORITHM])["sub"])


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _pa(test_engine, uid, tag, **kw) -> PaymentApplication:
    async with _factory(test_engine)() as db:
        vendor = Vendor(code=f"V-{tag[:6]}", name=f"B5 Vendor {tag}", category="Services",
                        contact_name="A", contact_email="a@t.test")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(number=f"PO-B5-{tag}", title="p", type=2, status="issued",
                           subtotal=Decimal("5"), total=Decimal("5"), vendor_id=vendor.id,
                           vendor_name=vendor.name, created_by=uid)
        db.add(po)
        await db.flush()
        fields = dict(
            pa_number=f"PA-B5-{tag}", title="b5", po_id=po.id, po_number=po.number,
            vendor_id=vendor.id, vendor_name=vendor.name, invoice_ids=[], gr_ids=[],
            subtotal=Decimal("5"), payment_amount=Decimal("5"), status="in_review",
            created_by=uid)
        fields.update(kw)
        pa = PaymentApplication(**fields)
        db.add(pa)
        await db.commit()
        return pa


async def _user(test_engine, name, role) -> uuid.UUID:
    from app.crud import user as user_crud
    from app.schemas.auth import RegisterRequest
    async with _factory(test_engine)() as db:
        u = await user_crud.create(db, RegisterRequest(
            email=f"b5-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name=name, role=role))
        await db.commit()
        return u.id


async def _events(test_engine, doc_type, doc_id, number, rows):
    """rows: (step_idx, action, actor_id, actor_role, comment)"""
    base = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)
    async with _factory(test_engine)() as db:
        for i, (step, action, actor, role, comment) in enumerate(rows):
            db.add(ApprovalEvent(
                document_type=doc_type, document_id=doc_id, document_number=number,
                step_idx=step, action=action, actor_id=actor, actor_role=role,
                comment=comment, created_at=base + timedelta(minutes=i)))
        await db.commit()


# ── ISS-0077 ──────────────────────────────────────────────────────────────────

def test_engine_comments_are_classified():
    assert event_kind("approve", ENGINE_SKIP_NO_DIRECTOR) == "skipped"
    assert event_kind("approve", ENGINE_SKIP_DIRECTOR_INACTIVE) == "skipped"
    assert event_kind("approve", ENGINE_RESYNC) == "skipped"
    assert event_kind("approve", "[reconstructed] " + ENGINE_SKIP_NO_DIRECTOR) == "skipped"
    assert event_kind("approve", ENGINE_SAME_APPROVER) == "same_approver"
    assert event_kind("approve", PMS_IMPORT) == "imported"
    assert event_kind("approve", "Looks fine") == "manual"
    assert event_kind("approve", None) == "manual"
    # Only approve events are machine-written.
    assert event_kind("reject", "Auto-skipped (typed by a person)") == "manual"
    assert event_kind("submit", None) == "manual"


def test_skip_reason_drops_the_marker_and_keeps_when():
    assert skip_reason(ENGINE_SKIP_NO_DIRECTOR) == "department has no Director"
    assert skip_reason(ENGINE_SKIP_DIRECTOR_INACTIVE) == "⚠ configured Director is inactive/missing"
    assert skip_reason(ENGINE_RESYNC) == (
        "over-budget approval no longer required — Budget Config mode changed to Allow "
        "without extra approval — Re-synced (config change)")
    assert skip_reason("Looks fine") is None


def test_pdf_signatories_and_history_share_one_definition():
    """The PDF leaves out exactly the approvals History shows without a name."""
    from app.crud import signatories
    assert signatories.is_machine_approval is is_machine_approval
    for comment in (ENGINE_SKIP_NO_DIRECTOR, ENGINE_RESYNC, PMS_IMPORT):
        assert is_machine_approval(comment)
        assert event_kind("approve", comment) in ("skipped", "imported")
    assert not is_machine_approval(ENGINE_SAME_APPROVER)
    assert MACHINE_APPROVAL_MARKERS == ("Auto-skipped", "Auto-approved on behalf of")


async def test_pa_events_say_skipped_not_approved_by_the_trigger(test_engine, admin_client):
    """PA-20260923-0017 / PR-20261015-0006 shape: the Department Manager approves,
    the Director step is skipped under the manager's id, then a config re-sync
    skips the Finance Manager step under the requester's id."""
    uid = _uid(admin_client)
    manager = await _user(test_engine, "Farshid Mohammadi", "dept_manager")
    requester = uid
    tag = uuid.uuid4().hex[:8]
    pa = await _pa(test_engine, uid, tag)
    await _events(test_engine, "pa", pa.id, pa.pa_number, [
        (0, "submit", requester, "requester", None),
        (0, "approve", manager, "dept_manager", "ok"),
        (1, "approve", manager, "director", ENGINE_SKIP_NO_DIRECTOR),
        (2, "approve", requester, "finance_manager", ENGINE_RESYNC),
    ])
    r = await admin_client.get(f"/api/v1/pa/{pa.id}/events")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [e["kind"] for e in rows] == ["manual", "manual", "skipped", "skipped"]
    assert rows[1]["skip_reason"] is None
    assert rows[2]["skip_reason"] == "department has no Director"
    assert rows[3]["skip_reason"].startswith("over-budget approval no longer required")
    # The raw record is unchanged — the engine's data is not rewritten.
    assert rows[2]["action"] == "approve" and rows[2]["comment"] == ENGINE_SKIP_NO_DIRECTOR
