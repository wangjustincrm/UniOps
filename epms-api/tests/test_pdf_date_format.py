"""PA PDF dates print as YYYY-MMM-DD on the plant's calendar (UI-DATE-002).

Timestamps are read in the plant's zone (Company Settings → Time Zone, default
America/Toronto): an approval at 21:30 on 30 October in Toronto is 01:30 UTC
on the 31st and must still print as the 30th. A calendar date (Expected
Settlement) is printed as is. The footer no longer says "UTC".
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
async def test_pa_pdf_dates_are_plant_days_yyyy_mmm_dd(test_engine):
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
            # 21:30 EDT on the 30th / 15:00 EDT on the 31st.
            submitted_at=datetime(2026, 10, 31, 1, 30, tzinfo=timezone.utc),
            approved_at=datetime(2026, 10, 31, 19, 0, tzinfo=timezone.utc),
            line_items=[PaLineItem(
                description="Widget", qty=Decimal("1"), unit="ea",
                unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0)],
        )
        db.add(pa)
        await db.flush()

        shown = _shown_text(generate_pa_pdf(
            pa, "Test Co", None, None, "PA Date Applicant",
            approvals=[{"role": "Finance Manager", "name": "Fiona Finance",
                        "at": datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)}],   # 22:00 EDT 31st
        ))
    assert "2026-OCT-30" in shown   # Date Submitted — the plant's day, not UTC's 31st
    assert "2026-OCT-31" in shown   # Date Approved + the approvals table
    assert "2026-NOV-01" not in shown   # the approval row's UTC day
    assert "2026-DEC-15" in shown   # Expected Settlement (a calendar date)
    assert "30/10/2026" not in shown
    assert "2026-10-30" not in shown
    assert "UTC" not in shown       # footer "Generated …" is plant time, no zone
