"""Requester dashboard KPIs:
  * Active PRs = in-progress PRs only (excludes issued/cancelled/rejected).
  * Pending Payments / Paid This Month follow the requester's OWN chain
    (my PR → PO → PA), not PAs the requester personally created.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.crud import dashboard as dash
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.user import User
from app.models.vendor import Vendor

TAG = uuid.uuid4().hex[:8]


def _kpi(resp, title):
    return next(k.value for k in resp.kpis if k.title == title)


async def test_requester_kpis_scope_and_active(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    me = uuid.uuid4()
    other = uuid.uuid4()
    ids = {"pr": [], "po": [], "pa": [], "vendor": None}
    async with sf() as db:
        db.add_all([
            User(id=me, email=f"me-{TAG}@x.com", hashed_password=hash_password("x"),
                 full_name="Me", role="requester", is_active=True),
            User(id=other, email=f"other-{TAG}@x.com", hashed_password=hash_password("x"),
                 full_name="Other", role="requester", is_active=True),
        ])
        vendor = Vendor(id=uuid.uuid4(), code=f"V{TAG}", name="V", category="general",
                        contact_name="N/A", contact_email="v@x.com", payment_terms="net30")
        db.add(vendor); ids["vendor"] = vendor.id
        await db.flush()

        def pr(status, owner):
            p = PurchaseRequest(id=uuid.uuid4(), number=f"PR-{TAG}-{uuid.uuid4().hex[:4]}",
                                title="t", type=2, status=status, created_by=owner)
            ids["pr"].append(p.id); return p
        pr_active = pr("in_review", me)     # counts
        pr_issued = pr("issued", me)        # excluded (completed)
        pr_other = pr("in_review", other)   # not mine
        db.add_all([pr_active, pr_issued, pr_other])
        await db.flush()

        def po(pr_id):
            o = PurchaseOrder(id=uuid.uuid4(), number=f"PO-{TAG}-{uuid.uuid4().hex[:4]}",
                              title="t", type=2, vendor_id=vendor.id, vendor_name="V",
                              created_by=me, pr_id=pr_id, total=Decimal("0"), subtotal=Decimal("0"))
            ids["po"].append(o.id); return o
        po_mine = po(pr_active.id)
        po_other = po(pr_other.id)
        db.add_all([po_mine, po_other])
        await db.flush()

        def pa(po_id, status, amt, creator):
            a = PaymentApplication(id=uuid.uuid4(), pa_number=f"PA-{TAG}-{uuid.uuid4().hex[:4]}",
                                   title="t", po_id=po_id, vendor_id=vendor.id, vendor_name="V",
                                   subtotal=Decimal(amt), payment_amount=Decimal(amt), status=status,
                                   created_by=creator)
            ids["pa"].append(a.id); return a
        db.add_all([
            # In my chain, created by someone else (an AP clerk) — must still count.
            pa(po_mine.id, "processed", "100", other),   # paid this month (fresh insert → updated_at=now)
            pa(po_mine.id, "in_review", "50", other),    # pending
            # Out of my chain but created by ME — must NOT count (old created_by logic would).
            pa(po_other.id, "processed", "999", me),
        ])
        await db.commit()

    try:
        async with sf() as db:
            resp = await dash.build_requester(db, me)
        assert _kpi(resp, "Active PRs") == "1"
        assert _kpi(resp, "Paid This Month") == "CAD 100.00"
        assert _kpi(resp, "Pending Payments") == "CAD 50.00"
    finally:
        async with sf() as db:
            await db.execute(sa_delete(PaymentApplication).where(PaymentApplication.id.in_(ids["pa"])))
            await db.execute(sa_delete(PurchaseOrder).where(PurchaseOrder.id.in_(ids["po"])))
            await db.execute(sa_delete(PurchaseRequest).where(PurchaseRequest.id.in_(ids["pr"])))
            await db.execute(sa_delete(Vendor).where(Vendor.id == ids["vendor"]))
            await db.execute(sa_delete(User).where(User.id.in_([me, other])))
            await db.commit()
