"""Time-scoped dashboard KPIs must key on the business-event timestamp, never on
updated_at (onupdate=now, bumped by any unrelated write):

  * Finance BP "Approved Today"      → PaymentApplication.approved_at
  * Warehouse  "Completed This Month" → GoodsReceipt.collected_at

Each test inserts one in-window row and one out-of-window row whose updated_at is
now (fresh insert) — the old updated_at proxy would wrongly count the latter.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.crud import dashboard as dash
from app.models.gr import GoodsReceipt
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.user import User
from app.models.vendor import Vendor

TAG = uuid.uuid4().hex[:8]


def _kpi(resp, title):
    return next(k.value for k in resp.kpis if k.title == title)


async def test_finance_bp_approved_today_keys_on_approved_at(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    me = uuid.uuid4()
    ids = {"pa": [], "vendor": None}
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=3)   # unambiguously not "today"
    async with sf() as db:
        db.add(User(id=me, email=f"fbp-{TAG}@x.com", hashed_password=hash_password("x"),
                    full_name="FBP", role="finance_bp", is_active=True))
        vendor = Vendor(id=uuid.uuid4(), code=f"VF{TAG}", name="V", category="general",
                        contact_name="N/A", contact_email="v@x.com", payment_terms="net30")
        db.add(vendor); ids["vendor"] = vendor.id
        await db.flush()

        def pa(amt, approved_at):
            a = PaymentApplication(id=uuid.uuid4(), pa_number=f"PA-{TAG}-{uuid.uuid4().hex[:4]}",
                                   title="t", po_id=None, vendor_id=vendor.id, vendor_name="V",
                                   subtotal=Decimal(amt), payment_amount=Decimal(amt),
                                   status="approved", created_by=me, approved_at=approved_at)
            ids["pa"].append(a.id); return a
        db.add_all([
            pa("100", now),         # approved today → counts
            pa("999", yesterday),   # approved days ago, fresh insert (updated_at=now) → must NOT count
        ])
        await db.commit()

    try:
        async with sf() as db:
            resp = await dash.build_finance_bp(db)
        assert _kpi(resp, "Approved Today") == "1"
    finally:
        async with sf() as db:
            await db.execute(sa_delete(PaymentApplication).where(PaymentApplication.id.in_(ids["pa"])))
            await db.execute(sa_delete(Vendor).where(Vendor.id == ids["vendor"]))
            await db.execute(sa_delete(User).where(User.id == me))
            await db.commit()


async def test_warehouse_completed_this_month_keys_on_collected_at(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    me = uuid.uuid4()
    ids = {"gr": [], "po": None, "pr": None, "vendor": None}
    now = datetime.now(timezone.utc)
    prior_month = now - timedelta(days=90)   # unambiguously a past month
    async with sf() as db:
        db.add(User(id=me, email=f"wh-{TAG}@x.com", hashed_password=hash_password("x"),
                    full_name="WH", role="warehouse_staff", is_active=True))
        vendor = Vendor(id=uuid.uuid4(), code=f"VW{TAG}", name="V", category="general",
                        contact_name="N/A", contact_email="v@x.com", payment_terms="net30")
        db.add(vendor); ids["vendor"] = vendor.id
        await db.flush()
        pr = PurchaseRequest(id=uuid.uuid4(), number=f"PR-{TAG}-{uuid.uuid4().hex[:4]}",
                             title="t", type=2, status="issued", created_by=me)
        db.add(pr); ids["pr"] = pr.id
        await db.flush()
        po = PurchaseOrder(id=uuid.uuid4(), number=f"PO-{TAG}-{uuid.uuid4().hex[:4]}",
                           title="t", type=2, vendor_id=vendor.id, vendor_name="V",
                           created_by=me, pr_id=pr.id, total=Decimal("0"), subtotal=Decimal("0"))
        db.add(po); ids["po"] = po.id
        await db.flush()

        def gr(collected_at):
            g = GoodsReceipt(id=uuid.uuid4(), number=f"GR-{TAG}-{uuid.uuid4().hex[:4]}",
                             title="t", po_id=po.id, po_number=po.number, vendor_id=vendor.id,
                             vendor_name="V", gr_type="physical", procurement_type=2,
                             status="confirmed", created_by=me, collected_at=collected_at)
            ids["gr"].append(g.id); return g
        db.add_all([
            gr(now),           # completed this month → counts
            gr(prior_month),   # completed months ago, fresh insert (updated_at=now) → must NOT count
        ])
        await db.commit()

    try:
        async with sf() as db:
            resp = await dash.build_warehouse(db)
        assert _kpi(resp, "Completed This Month") == "1"
    finally:
        async with sf() as db:
            await db.execute(sa_delete(GoodsReceipt).where(GoodsReceipt.id.in_(ids["gr"])))
            await db.execute(sa_delete(PurchaseOrder).where(PurchaseOrder.id == ids["po"]))
            await db.execute(sa_delete(PurchaseRequest).where(PurchaseRequest.id == ids["pr"]))
            await db.execute(sa_delete(Vendor).where(Vendor.id == ids["vendor"]))
            await db.execute(sa_delete(User).where(User.id == me))
            await db.commit()
