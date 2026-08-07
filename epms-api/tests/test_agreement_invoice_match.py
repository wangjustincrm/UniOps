"""Invoice ↔ Agreement matching (Phase 1A: manual route, no slips)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.invoice import Invoice
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio


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
