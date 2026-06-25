"""expense_line_items.tax_code roundtrip (Phase 0-B2)."""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.expense import ExpenseClaim, ExpenseLineItem
from app.schemas.expense import LineItemCreate


async def test_line_item_tax_code_roundtrip(db_session):
    claim = ExpenseClaim(
        claim_number=f"EXP-{uuid.uuid4().hex[:8]}", claim_type="EXP",
        employee_id=uuid.uuid4(), employee_name="Jane Doe",
        submission_date=date(2026, 6, 11), currency="CAD",
        total_amount=Decimal("113.00"), tax_amount=Decimal("13.00"),
        net_amount=Decimal("100.00"), status="draft", created_by=uuid.uuid4(),
    )
    db_session.add(claim)
    await db_session.flush()

    li = LineItemCreate(
        line_number=1, expense_date=date(2026, 6, 10), description="Lab supplies",
        budget_account_id=uuid.uuid4(), budget_account_code="6100",
        budget_account_name="Lab", total_amount=Decimal("113.00"),
        tax_amount=Decimal("13.00"), net_amount=Decimal("100.00"),
        tax_code="HST_ON",
    )
    db_session.add(ExpenseLineItem(claim_id=claim.id, **li.model_dump()))
    await db_session.flush()

    row = (await db_session.execute(
        select(ExpenseLineItem).where(ExpenseLineItem.claim_id == claim.id)
    )).scalar_one()
    assert row.tax_code == "HST_ON"


async def test_tax_code_is_optional(db_session):
    li = LineItemCreate(
        line_number=1, expense_date=date(2026, 6, 10), description="No tax code",
        budget_account_id=uuid.uuid4(), budget_account_code="6100",
        budget_account_name="Lab", total_amount=Decimal("10.00"),
        tax_amount=Decimal("0"), net_amount=Decimal("10.00"),
    )
    assert li.tax_code is None
