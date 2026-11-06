"""ISS-0137 / PA-007: when Notification Settings → "PA Approved — Notify
Requester" is on, the requester gets an email once the PA is finally
approved. Off by default. An FYI email, never a Task."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.crud import user as user_crud
from app.crud.config import get_or_create as get_config
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.services.notification import send_pa_approved

pytestmark = pytest.mark.asyncio


async def _setup(db, *, enabled: bool | None, status="approved"):
    cfg = await get_config(db)
    ns = dict(cfg.notification_settings or {})
    ns.pop("pa_approved_notify_requester", None)
    if enabled is not None:
        ns["pa_approved_notify_requester"] = enabled
    cfg.notification_settings = ns
    flag_modified(cfg, "notification_settings")
    requester = await user_crud.create(db, RegisterRequest(
        email=f"pareq-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Paula Requester", role="requester"))
    vendor = Vendor(code=f"V-PAN-{uuid.uuid4().hex[:6]}", name="Notify Vendor", category="x",
                    contact_name="c", contact_email="c@x.com", payment_terms="net30", currency="CAD")
    db.add(vendor)
    await db.flush()
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-NTF-{uuid.uuid4().hex[:6]}", title="t", status=status,
        vendor_id=vendor.id, vendor_name=vendor.name, invoice_ids=[], gr_ids=[],
        subtotal=Decimal("10"), payment_amount=Decimal("10"), created_by=requester.id,
    )
    db.add(pa)
    await db.commit()
    return requester, pa


@pytest.fixture
def sent(monkeypatch):
    out: list[tuple[str, str]] = []

    async def _fake(to, subject, html, **kw):
        out.append((to, subject))
    monkeypatch.setattr("app.services.email.send_email", _fake)
    return out


async def test_on_by_default(test_engine, sent):
    """PRD PA-007: the requester is notified — unless an admin switches it off."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester, pa = await _setup(db, enabled=None)
        await send_pa_approved(pa.id, db=db)
    assert [to for to, _ in sent] == [requester.email]


async def test_switched_off_emails_nobody(test_engine, sent):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        _, pa = await _setup(db, enabled=False)
        await send_pa_approved(pa.id, db=db)
    assert sent == []


async def test_on_emails_the_requester_and_raises_no_task(test_engine, sent):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester, pa = await _setup(db, enabled=True)
        await send_pa_approved(pa.id, db=db)
        tasks = (await db.execute(select(Task).where(Task.document_id == pa.id))).scalars().all()
    assert sent == [(requester.email, f"Your PA {pa.pa_number} has been approved")]
    assert tasks == []


async def test_not_sent_unless_the_pa_is_approved(test_engine, sent):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        _, pa = await _setup(db, enabled=True, status="in_review")
        await send_pa_approved(pa.id, db=db)
    assert sent == []
