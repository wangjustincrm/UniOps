"""What the task emails say — PRD §3.9.2 / GR-N-001.

ISS-0140: the GR created / goods ready / approve PR / create PO emails lacked
the content those rules list (PO, vendor, delivery date, items with quantity
and condition; storage location; PR title, requester, step label, currency).

ISS-0145: the PR returned-for-revision email went out as "Comment: {comment}"
and never said who returned it. The engine writes the task with no room for
either; the dispatcher now reads them from the return's approval event.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import app.db.session as session_module
from app.crud import user as user_crud
from app.models.approval import ApprovalEvent
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.services import notification


@pytest.fixture
def mails(monkeypatch):
    sent: list[tuple[str, str, str]] = []

    async def _fake_send_email(to, subject, html, **kwargs):
        sent.append((to, subject, html))

    monkeypatch.setattr("app.services.email.send_email", _fake_send_email)
    return sent


def _mine(mails, user) -> list[tuple[str, str, str]]:
    return [m for m in mails if m[0] == user.email]


async def _user(db, full_name: str, role: str = "requester"):
    u = await user_crud.create(db, RegisterRequest(
        email=f"mail-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=full_name, role=role))
    await db.commit()
    return u


async def _pr(db, owner, **kw) -> PurchaseRequest:
    pr = PurchaseRequest(number=f"PR-MAIL-{uuid.uuid4().hex[:6]}", title=kw.pop("title", "Office chairs"),
                         type=3, status=kw.pop("status", "returned"), created_by=owner.id, **kw)
    db.add(pr)
    await db.commit()
    return pr


async def _send(db, task: Task) -> None:
    db.add(task)
    await db.commit()
    await notification.dispatch_task_notification(task, db)
    await db.commit()


async def test_returned_pr_email_shows_the_comment_and_who_returned_it(mails):
    async with session_module.AsyncSessionLocal() as db:
        requester = await _user(db, "Rita Requester")
        manager = await _user(db, "Manny Manager", role="dept_manager")
        pr = await _pr(db, requester)
        db.add(ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                             step_idx=0, action="return", actor_id=manager.id,
                             actor_role="dept_manager", comment="Just for test <b>now</b>"))
        await _send(db, Task(
            type="revise_pr", document_type="pr", document_id=pr.id, document_number=pr.number,
            assigned_role="requester", assigned_user_id=requester.id,
            title=f"Revise PR: {pr.number} — {pr.title}",
            description="Your PR has been returned for revision."))

    [(_, subject, body)] = _mine(mails, requester)
    assert subject == f"Your PR {pr.number} has been returned for revision"
    assert "{comment}" not in body and "{returned_by}" not in body
    assert "Just for test &lt;b&gt;now&lt;/b&gt;" in body          # escaped (NTF-008)
    assert "<b>Returned by:</b> Manny Manager" in body


async def test_the_latest_return_is_the_one_quoted(mails):
    async with session_module.AsyncSessionLocal() as db:
        requester = await _user(db, "Rita Requester")
        manager = await _user(db, "Manny Manager", role="dept_manager")
        pr = await _pr(db, requester)
        db.add(ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                             step_idx=0, action="return", actor_id=manager.id,
                             actor_role="dept_manager", comment="nonono"))
        await db.commit()
        db.add(ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                             step_idx=0, action="return", actor_id=manager.id,
                             actor_role="dept_manager", comment="Just for test"))
        await db.commit()
        await _send(db, Task(
            type="revise_pr", document_type="pr", document_id=pr.id, document_number=pr.number,
            assigned_role="requester", assigned_user_id=requester.id, title="Revise PR"))

    [(_, _, body)] = _mine(mails, requester)
    assert "<b>Comment:</b> Just for test" in body and "nonono" not in body


async def test_returned_signoff_email_shows_the_comment(mails):
    """Same gap on po_signoff_returned (revise_po_signoff, document_type posign)."""
    async with session_module.AsyncSessionLocal() as db:
        submitter = await _user(db, "Sam Submitter")
        signer = await _user(db, "Opal OPM", role="opm")
        po_id = uuid.uuid4()
        db.add(ApprovalEvent(document_type="posign", document_id=po_id, document_number="PO-SIGN-1",
                             step_idx=1, action="return", actor_id=signer.id,
                             actor_role="opm", comment="Wrong vendor"))
        await _send(db, Task(
            type="revise_po_signoff", document_type="posign", document_id=po_id,
            document_number="PO-SIGN-1", assigned_role="requester",
            assigned_user_id=submitter.id, title="Revise PO sign-off: PO-SIGN-1"))

    [(_, _, body)] = _mine(mails, submitter)
    assert "<b>Comment:</b> Wrong vendor" in body and "{comment}" not in body


# ── ISS-0140: PRD 3.9.2 / GR-N-001 content ──────────────────────────────────

async def test_approve_pr_email_has_title_requester_currency_and_step(mails):
    async with session_module.AsyncSessionLocal() as db:
        requester = await _user(db, "Rita Requester")
        approver = await _user(db, "Dora Director", role="director")
        pr = await _pr(db, requester, title="Pump <spare> parts", status="in_review",
                       currency="USD")
        await _send(db, Task(
            type="approve_pr", document_type="pr", document_id=pr.id, document_number=pr.number,
            assigned_role="director", assigned_user_id=approver.id,
            title=f"Approve PR: {pr.number} — {pr.title}",
            description="Step 2/2: Director review required.",
            amount=Decimal("1250.50"), vendor="Acme"))

    [(_, _, body)] = _mine(mails, approver)
    assert "<b>Title:</b> Pump &lt;spare&gt; parts" in body
    assert "<b>Requester:</b> Rita Requester" in body
    assert "<b>Amount:</b> USD 1250.50" in body
    assert "<b>Approval step:</b> Director (step 2 of 2)" in body
    assert "{" not in body


def test_step_label_reads_the_engines_description():
    assert notification._step_label("Step 1/3: Department Manager review required.") \
        == "Department Manager (step 1 of 3)"
    assert notification._step_label("Step 2/2: Over-Budget — GM / OPM review required.") \
        == "Over-Budget — GM / OPM (step 2 of 2)"
    assert notification._step_label(
        "⚠️ OVER-BUDGET PRE-APPROVAL — PR amount: 10\nJustification: x") == "Over-budget pre-approval"
    assert notification._step_label(None) == "—"


async def test_create_po_email_has_the_pr_title_and_currency(mails):
    async with session_module.AsyncSessionLocal() as db:
        requester = await _user(db, "Rita Requester")
        officer = await _user(db, "Polly Procurement", role="procurement_officer")
        pr = await _pr(db, requester, title="Lab gloves", status="approved")
        await _send(db, Task(
            type="create_po", document_type="pr", document_id=pr.id, document_number=pr.number,
            assigned_role="procurement_officer", assigned_user_id=officer.id,
            title=f"Create PO: {pr.number} — {pr.title}", amount=Decimal("80"), vendor="Acme"))

    [(_, subject, body)] = _mine(mails, officer)
    assert subject == f"Action Required: Create PO for PR {pr.number}"
    assert "<b>Title:</b> Lab gloves" in body and "<b>Vendor:</b> Acme" in body
    assert "<b>Amount:</b> CAD 80" in body


async def _gr(db, owner, *, storage_location=None):
    v = Vendor(code=f"V-MAIL-{uuid.uuid4().hex[:6]}", name="Acme <Supply>", category="x",
               contact_name="c", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    pr = await _pr(db, owner, status="approved")
    po = PurchaseOrder(number=f"PO-MAIL-{uuid.uuid4().hex[:6]}", title="T", type=3,
                       vendor_id=v.id, vendor_name=v.name, status="issued",
                       created_by=owner.id, pr_id=pr.id, total=Decimal("30"))
    db.add(po)
    await db.flush()
    gr = GoodsReceipt(number=f"GR-MAIL-{uuid.uuid4().hex[:6]}", title="Delivery", po_id=po.id,
                      po_number=po.number, pr_id=pr.id, pr_number=pr.number, vendor_id=v.id,
                      vendor_name=v.name, gr_type="goods", procurement_type=3,
                      storage_location=storage_location, created_by=owner.id,
                      # 01:30 UTC on the 27th is still the 26th in Ontario.
                      received_at=datetime(2026, 10, 27, 1, 30, tzinfo=timezone.utc))
    gr.line_items = [
        GrLineItem(description="Gasket", qty_ordered=Decimal("10"), qty_received=Decimal("10.0000"),
                   unit="EA", unit_price=Decimal("1"), line_total=Decimal("10"), sort_order=0),
        GrLineItem(description="Valve", qty_ordered=Decimal("4"), qty_received=Decimal("2.5000"),
                   unit="KG", unit_price=Decimal("8"), line_total=Decimal("20"),
                   condition="damaged", sort_order=1),
    ]
    db.add(gr)
    await db.commit()
    return gr


async def test_gr_created_email_has_po_vendor_date_and_items(mails):
    async with session_module.AsyncSessionLocal() as db:
        owner = await _user(db, "Owen Owner")
        gr = await _gr(db, owner)
        await _send(db, Task(
            type="acknowledge_gr", document_type="gr", document_id=gr.id,
            document_number=gr.number, assigned_role="requester", assigned_user_id=owner.id,
            title=f"Acknowledge GR: {gr.number} — {gr.title}", vendor=gr.vendor_name))

    [(_, subject, body)] = _mine(mails, owner)
    assert subject == f"Goods Receipt Created — {gr.number}"
    assert f"<b>PO:</b> {gr.po_number}" in body
    assert "<b>Vendor:</b> Acme &lt;Supply&gt;" in body
    assert "<b>Delivery date:</b> 2026-OCT-26" in body   # plant day, UI-DATE-002
    assert "• Gasket — 10 EA (Good)" in body and "• Valve — 2.5 KG (Damaged)" in body
    assert "{" not in body


async def test_collection_email_has_the_storage_location(mails):
    async with session_module.AsyncSessionLocal() as db:
        owner = await _user(db, "Owen Owner")
        gr = await _gr(db, owner, storage_location="Technical Warehouse, bay 3")
        await _send(db, Task(
            type="collect_goods", document_type="gr", document_id=gr.id,
            document_number=gr.number, assigned_role="requester", assigned_user_id=owner.id,
            title=f"Collect Goods: {gr.number} — {gr.title}", vendor=gr.vendor_name))

    [(_, _, body)] = _mine(mails, owner)
    assert "<b>Storage location:</b> Technical Warehouse, bay 3" in body
    assert f"(PO <b>{gr.po_number}</b>)" in body


async def test_unedited_old_templates_upgrade_to_the_new_copy():
    """Seeded DBs (dev4, production) hold the old copies verbatim; templates
    are not editable on any screen, so they upgrade in place."""
    from sqlalchemy import update as sa_update
    from app.crud.config import (
        _DEFAULT_EMAIL_TEMPLATES, _SUPERSEDED_EMAIL_TEMPLATE_DEFAULTS, get_or_create)
    from app.models.config import CompanyConfig
    keys = ("pr_approval_request", "pr_returned", "po_creation_request",
            "gr_created", "gr_collection_ready")
    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_or_create(db)
        before = dict(cfg.email_templates or {})
        old = {**before, **{k: _SUPERSEDED_EMAIL_TEMPLATE_DEFAULTS[k] for k in keys}}
        await db.execute(sa_update(CompanyConfig).values(email_templates=old))
        await db.commit()
    try:
        async with session_module.AsyncSessionLocal() as db:
            cfg = await get_or_create(db)
            for k in keys:
                assert cfg.email_templates[k] == _DEFAULT_EMAIL_TEMPLATES[k], k
            await db.commit()
    finally:
        async with session_module.AsyncSessionLocal() as db:
            await db.execute(sa_update(CompanyConfig).values(email_templates=before))
            await db.commit()
