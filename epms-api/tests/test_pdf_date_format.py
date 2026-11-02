"""PA / PR PDF dates print as DD/MM/YYYY (UI-DATE-001, ISS-0183).

The PA PDF printed "Date Submitted 2026-10-30" while every page of the app
shows 30/10/2026. Format only: the same instant, the same calendar day.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.pa import PaLineItem, PaymentApplication
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.services.pdf_pa import generate_pa_pdf
from tests.test_pr_pdf_budget_account import _shown_text


@pytest.mark.asyncio
async def test_pa_pdf_dates_are_dd_mm_yyyy(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"pa-date-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="PA Date Applicant", role="requester"))
        vendor = Vendor(
            code=f"V{uuid.uuid4().hex[:6]}", name="PA Date Vendor", category="general",
            contact_name="Vendor Contact", contact_email="vendor@example.com")
        db.add(vendor)
        await db.flush()
        pa = PaymentApplication(
            pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Date format PA",
            vendor_id=vendor.id, vendor_name=vendor.name, pa_type="prepayment",
            prepayment_pct=Decimal("30"), expected_settlement_date=date(2026, 12, 15),
            subtotal=Decimal("10"), tax_amount=Decimal("0"), payment_amount=Decimal("10"),
            currency="CAD", status="approved", created_by=user.id,
            submitted_at=datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc),
            approved_at=datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc),
            line_items=[PaLineItem(
                description="Widget", qty=Decimal("1"), unit="ea",
                unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0)],
        )
        db.add(pa)
        await db.flush()

        shown = _shown_text(generate_pa_pdf(
            pa, "Test Co", None, None, "PA Date Applicant",
            approvals=[{"role": "Finance Manager", "name": "Fiona Finance",
                        "at": datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)}],
        ))
    assert "30/10/2026" in shown    # Date Submitted
    assert "31/10/2026" in shown    # Date Approved + the approvals table
    assert "15/12/2026" in shown    # Expected Settlement (prepayment)
    assert "2026-10-30" not in shown
    assert "2026-10-31" not in shown
    assert "2026-12-15" not in shown
