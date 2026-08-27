"""PA line prices print at the precision the column actually holds.

`pa_line_items.unit_price` is Numeric(15, 5) -- the NC-sourced five-decimal
purchase price, copied onto the PA from the PO line. pdf_po/pdf_gr were moved
onto unit_price_text() when the columns were widened; pdf_pa was not, so a PA
kept rendering a rounded price. Line totals stay at two decimals: those are
payable amounts.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.pa import PaLineItem, PaymentApplication
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.services.pdf_pa import generate_pa_pdf
from tests.test_pr_pdf_budget_account import _shown_text


async def _seed_pa(db: AsyncSession, unit_price: Decimal, line_total: Decimal) -> PaymentApplication:
    user = await user_crud.create(db, RegisterRequest(
        email=f"pa-price-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="PA Price Applicant", role="requester"))
    vendor = Vendor(
        code=f"V{uuid.uuid4().hex[:6]}", name="PA Price Vendor", category="general",
        contact_name="Vendor Contact", contact_email="vendor@example.com")
    db.add(vendor)
    await db.flush()
    pa = PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Five-decimal price PA",
        vendor_id=vendor.id, vendor_name=vendor.name, pa_type="regular",
        subtotal=line_total, tax_amount=Decimal("0"), payment_amount=line_total,
        currency="CAD", status="approved", created_by=user.id,
        line_items=[PaLineItem(
            description="Widget", qty=Decimal("1000.0000"), unit="ea",
            unit_price=unit_price, line_total=line_total, sort_order=0)],
    )
    db.add(pa)
    await db.flush()
    return pa


@pytest.mark.asyncio
async def test_five_decimal_price_is_not_rounded(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pa = await _seed_pa(db, Decimal("0.94425"), Decimal("944.25"))
        await db.commit()

        shown = _shown_text(generate_pa_pdf(pa, "Test Co", None, None))
        assert "0.94425" in shown       # the stored price, in full
        assert "944.25" in shown        # the line total stays a payable amount
        assert "1,000" in shown         # and the quantity is not "1E+3"


@pytest.mark.asyncio
async def test_two_decimal_price_keeps_two_decimals(test_engine):
    """Padding is trimmed only past the second place -- a price must not print
    as "50", which reads as rounded."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pa = await _seed_pa(db, Decimal("50.00000"), Decimal("50000.00"))
        await db.commit()

        shown = _shown_text(generate_pa_pdf(pa, "Test Co", None, None))
        assert "50.00" in shown
