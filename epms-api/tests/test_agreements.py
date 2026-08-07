"""Purchase Agreement — model, CRUD, approval, and match/PA integration."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def test_agreement_model_roundtrip(test_engine):
    # NOTE: the `seeded_vendor` / `system_user_id` conftest fixtures insert via a
    # separate, uncommitted psycopg2 connection (pg_cur) — every existing use of
    # them (tests/test_nc_purchase_writer.py) reads back through that same
    # connection. This test writes through the async ORM session (test_engine)
    # instead, which is a *different* Postgres connection and would never see
    # those uncommitted rows (FK violation, confirmed empirically). So the
    # vendor/user rows are seeded in-session here, matching the pattern used by
    # tests/test_backfill_invoice_gr_links.py.
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        vendor = Vendor(
            code=f"V-{uuid.uuid4().hex[:8]}", name="Princess Auto", category="supplier",
            contact_name="AP Contact", contact_email="ap@princessauto.example",
        )
        db.add(vendor)
        user = await user_crud.create(db, RegisterRequest(
            email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Agreement Tester", role="procurement_officer",
        ))
        await db.flush()

        agr = PurchaseAgreement(
            number=f"AGR-202608-{uuid.uuid4().hex[:4]}",
            title="Princess Auto house account",
            agreement_type="house_account",
            contract_no="CN-2026-001",
            contact_email="ap@princessauto.example",
            vendor_id=vendor.id,
            vendor_name=vendor.name,
            vendor_reference="PO-585-2606-01",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            not_to_exceed=Decimal("50000.00"),
            created_by=user.id,
        )
        db.add(agr)
        await db.commit()
        await db.refresh(agr)

    async with factory() as db:
        got = (await db.execute(
            select(PurchaseAgreement).where(PurchaseAgreement.id == agr.id)
        )).scalar_one()

    assert got.status == "draft"                    # server default
    assert got.grace_days == 30                     # server default
    assert got.consumed_amount == Decimal("0")      # server default
    assert got.currency == "CAD"
    assert got.approval_step_idx == 0
    assert got.vendor_reference == "PO-585-2606-01"
