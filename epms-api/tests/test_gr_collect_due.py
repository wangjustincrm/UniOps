"""ISS-0146 ②: Collection Config timers — the requester is reminded to collect,
then their Department Manager and every Finance Manager are told once. Active
only while "Collection Confirmation Required" is on (rule question 16, B)."""
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
from app.schemas.gr import GrActionRequest, GrCreate, GrLineItemIn
from app.tasks import gr_collect_due as mod

pytestmark = pytest.mark.asyncio


@pytest.fixture
def sessions(test_engine, monkeypatch):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)
    return factory


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def _send(to, subject, html, **kw):
        out.append((to, subject))
    monkeypatch.setattr("app.services.email.send_email", _send)
    return out


@pytest.fixture
async def collection(sessions):
    async with sessions() as db:
        cfg = await get_config(db)
        before = dict(cfg.collection_config or {})
        await db.commit()

    async def _set(on: bool):
        async with sessions() as db:
            cfg = await get_config(db)
            cfg.collection_config = {**before, "collection_gate_enabled": on, "reminder_days": 1,
                                     "manager_escalation_days": 3, "fm_alert_days": 5}
            flag_modified(cfg, "collection_config")
            await db.commit()
    yield _set
    async with sessions() as db:
        cfg = await get_config(db)
        cfg.collection_config = before
        flag_modified(cfg, "collection_config")
        await db.commit()


async def _user(db, role, dept=None):
    u = await user_crud.create(db, RegisterRequest(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=f"T {role}", role=role))
    u.department_id = dept
    return u


async def _waiting_collection(db, *, days: int):
    dept = Department(code=f"D{uuid.uuid4().hex[:6]}", name="Ops")
    db.add(dept)
    await db.flush()
    owner = await _user(db, "requester", dept.id)
    manager = await _user(db, "dept_manager", dept.id)
    fm = await _user(db, "finance_manager")
    v = Vendor(code=f"V-COL-{uuid.uuid4().hex[:6]}", name="Col Vendor", category="x",
               contact_name="c", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    pr = PurchaseRequest(number=f"PR-COL-{uuid.uuid4().hex[:6]}", title="T", type=3,
                         status="approved", created_by=owner.id)
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(number=f"PO-COL-{uuid.uuid4().hex[:6]}", title="T", type=3,
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
    await gr_crud.action(db, gr, GrActionRequest(action="acknowledge"), actor_id=owner.id)
    gr.received_at = datetime.now(timezone.utc) - timedelta(days=days)
    await db.commit()
    assert gr.status == "collection_pending"
    return gr, owner, manager, fm


async def test_inactive_while_collection_not_required(sessions, sent, collection):
    async with sessions() as db:
        await _waiting_collection(db, days=9)
    await collection(False)
    await mod.run_gr_collect_due()
    assert sent == []


async def test_reminds_escalates_and_alerts_once(sessions, sent, collection):
    async with sessions() as db:
        gr, owner, manager, fm = await _waiting_collection(db, days=6)
    await collection(True)

    def mine():   # the shared test database may hold other waiting receipts
        return [t for t, s in sent if gr.po_number in s]

    await mod.run_gr_collect_due()          # 1st: reminder only
    assert owner.email in mine() and manager.email not in mine() and fm.email not in mine()

    await mod.run_gr_collect_due()          # 2nd: reminded already, 6 ≥ 3 and ≥ 5
    await mod.run_gr_collect_due()          # 3rd: no repeat for the manager / FM
    assert mine().count(owner.email) == 3
    assert mine().count(manager.email) == 1
    assert mine().count(fm.email) == 1


async def test_overdue_cutoff_follows_the_fm_alert_days(sessions, collection):
    await collection(True)
    async with sessions() as db:
        cfg = await get_config(db)
        now = datetime(2026, 10, 30, tzinfo=timezone.utc)
        assert mod.collection_overdue_cutoff(cfg, now) == now - timedelta(days=5)
    await collection(False)
    async with sessions() as db:
        assert mod.collection_overdue_cutoff(await get_config(db)) is None
