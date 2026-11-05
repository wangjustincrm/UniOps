"""ISS-0146 ①: GR Notification SLA — a goods receipt waiting for
acknowledgement is reminded to the PR owner, then escalated once to their
Department Manager. Off by default, dry run first (same gates as the service
completion reminder)."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

import app.db.session as session_module
from app.crud import gr as gr_crud
from app.crud import user as user_crud
from app.crud.config import get_or_create as get_config
from app.models.department import Department
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.schemas.gr import GrCreate, GrLineItemIn
from app.tasks import gr_ack_due as mod

pytestmark = pytest.mark.asyncio


@pytest.fixture
def sessions(test_engine, monkeypatch):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)
    return factory


@pytest.fixture
def sent(monkeypatch):
    out = {"emails": [], "admin": [], "cc": []}

    async def _send(to, subject, html, **kw):
        out["emails"].append((to, subject))
        if kw.get("cc"):
            out["cc"].append((to, kw["cc"]))

    async def _admin(subject, body, db=None):
        out["admin"].append(subject)
    monkeypatch.setattr("app.services.email.send_email", _send)
    monkeypatch.setattr(mod, "send_admin_alert", _admin)
    return out


@pytest.fixture
async def settings(sessions):
    async with sessions() as db:
        cfg = await get_config(db)
        before_ns = dict(cfg.notification_settings or {})
        before_sla = dict(cfg.gr_notification_sla or {})
        await db.commit()

    async def _set(**ns):
        async with sessions() as db:
            cfg = await get_config(db)
            cfg.notification_settings = {**before_ns, **ns}
            cfg.gr_notification_sla = {"reminder_days": 1, "manager_escalation_days": 3}
            flag_modified(cfg, "notification_settings")
            flag_modified(cfg, "gr_notification_sla")
            await db.commit()
    yield _set
    async with sessions() as db:
        cfg = await get_config(db)
        cfg.notification_settings = before_ns
        cfg.gr_notification_sla = before_sla
        flag_modified(cfg, "notification_settings")
        flag_modified(cfg, "gr_notification_sla")
        await db.commit()


async def _waiting_gr(db, *, days: int):
    dept = Department(code=f"D{uuid.uuid4().hex[:6]}", name="Ops")
    db.add(dept)
    await db.flush()
    owner = await user_crud.create(db, RegisterRequest(
        email=f"own-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Owen Owner", role="requester"))
    owner.department_id = dept.id
    manager = await user_crud.create(db, RegisterRequest(
        email=f"mgr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Mia Manager", role="dept_manager"))
    manager.department_id = dept.id
    v = Vendor(code=f"V-ACK-{uuid.uuid4().hex[:6]}", name="Ack Vendor", category="x",
               contact_name="c", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    pr = PurchaseRequest(number=f"PR-ACK-{uuid.uuid4().hex[:6]}", title="T", type=3,
                         status="approved", created_by=owner.id)
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(number=f"PO-ACK-{uuid.uuid4().hex[:6]}", title="T", type=3,
                       vendor_id=v.id, vendor_name=v.name, status="issued",
                       created_by=owner.id, pr_id=pr.id, total=Decimal("10"))
    db.add(po)
    await db.flush()
    line = PoLineItem(po_id=po.id, description="Part", qty=Decimal("1"), unit="EA",
                      unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0)
    db.add(line)
    await db.flush()
    gr = await gr_crud.create(db, GrCreate(po_id=po.id, title="Delivery", line_items=[GrLineItemIn(
        po_line_id=line.id, description="Part", qty_ordered=Decimal("1"),
        qty_received=Decimal("1"), unit="EA", unit_price=Decimal("10"))]), po, owner.id)
    gr.received_at = datetime.now(timezone.utc) - timedelta(days=days)
    await db.commit()
    return gr, owner, manager


async def test_scan_waits_past_the_reminder_days(sessions):
    async with sessions() as db:
        fresh, _, _ = await _waiting_gr(db, days=1)
        stale, _, _ = await _waiting_gr(db, days=2)
        found = {d.gr.id for d in await mod.scan_unacknowledged(db, reminder_days=1)}
    assert stale.id in found and fresh.id not in found


async def test_off_and_dry_run_remind_nobody(sessions, sent, settings):
    async with sessions() as db:
        _, owner, _ = await _waiting_gr(db, days=5)
    await settings(gr_ack_due_enabled=False)
    await mod.run_gr_ack_due()
    assert sent == {"emails": [], "admin": [], "cc": []}

    await settings(gr_ack_due_enabled=True)          # dry run is the default
    await mod.run_gr_ack_due()
    assert owner.email not in [to for to, _ in sent["emails"]]
    assert len(sent["admin"]) == 1 and "Dry run" in sent["admin"][0]


async def test_reminds_then_escalates_once(sessions, sent, settings):
    async with sessions() as db:
        _, owner, manager = await _waiting_gr(db, days=5)
    await settings(gr_ack_due_enabled=True, gr_ack_due_dry_run=False)

    await mod.run_gr_ack_due()          # 1st: reminder only (never reminded before)
    to = [t for t, _ in sent["emails"]]
    assert owner.email in to and manager.email not in to

    await mod.run_gr_ack_due()          # 2nd: reminded already, 5 ≥ 3 days → escalate
    await mod.run_gr_ack_due()          # 3rd: escalation is not repeated
    to = [t for t, _ in sent["emails"]]
    assert to.count(manager.email) == 1
    assert to.count(owner.email) == 3
    # PRD GR-N-003: the escalation copies the requester.
    assert sent["cc"].count((manager.email, owner.email)) == 1


async def test_acknowledged_gr_is_not_chased(sessions, sent, settings):
    from app.schemas.gr import GrActionRequest

    async with sessions() as db:
        gr, owner, _ = await _waiting_gr(db, days=5)
        await gr_crud.action(db, gr, GrActionRequest(action="acknowledge"), actor_id=owner.id)
        await db.commit()
    await settings(gr_ack_due_enabled=True, gr_ack_due_dry_run=False)
    await mod.run_gr_ack_due()
    assert owner.email not in [t for t, _ in sent["emails"]]
