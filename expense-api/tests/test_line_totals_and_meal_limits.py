"""Line amounts must reconcile, and the TRV over-limit flag must survive an edit.

Two holes on the money path:

* `_compute_totals_exp` summed the client's total / tax / net columns
  independently and never checked they described the same line. A claim with
  net=10, tax=1, total=1000 displayed as ten dollars of expense and paid a
  thousand — payment reads total_amount. (MIL never had this: its totals are
  recomputed from distance x rate.)
* the TRV meal per-diem check ran in create_claim only. Raise a compliant TRV,
  get it returned, edit the meals up past the caps, resubmit: is_over_budget
  stayed False, and over-limit is what injects the conditional Finance Manager
  step (PRD WF-002).
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.crud import expense as expense_crud
from app.schemas.expense import (
    ExpenseClaimCreate, ExpenseClaimUpdate, LineItemCreate,
)

_ACCOUNT = dict(
    budget_account_id=uuid.uuid4(),
    budget_account_code="GA00101",
    budget_account_name="Travel & Entertainment",
)


def _line(n: int, *, description: str, net: str, tax: str, total: str) -> dict:
    return dict(
        line_number=n, expense_date=date(2026, 9, 10), description=description,
        net_amount=Decimal(net), tax_amount=Decimal(tax), total_amount=Decimal(total),
        **_ACCOUNT,
    )


# ── total = net + tax ─────────────────────────────────────────────────────────

def test_a_line_whose_total_does_not_match_is_refused():
    with pytest.raises(ValidationError) as exc:
        LineItemCreate(**_line(1, description="Hotel", net="10.00", tax="1.00",
                               total="1000.00"))
    assert "does not equal" in str(exc.value)


def test_a_consistent_line_is_accepted():
    li = LineItemCreate(**_line(1, description="Hotel", net="100.00", tax="13.00",
                                total="113.00"))
    assert li.total_amount == Decimal("113.00")


def test_a_cent_of_rounding_slack_is_tolerated():
    """OCR-filled lines derive net as total - tax and can land a cent out."""
    li = LineItemCreate(**_line(1, description="Taxi", net="100.00", tax="12.99",
                                total="113.00"))
    assert li.net_amount == Decimal("100.00")


def test_negative_lines_are_still_allowed():
    """Credit / refund lines are legitimate here — PR/PO take negative unit
    prices and GR/invoices take discount lines, so this must not become a
    non-negative check."""
    li = LineItemCreate(**_line(1, description="Refund of duplicate charge",
                                net="-50.00", tax="-6.50", total="-56.50"))
    assert li.total_amount == Decimal("-56.50")


async def test_the_claim_total_now_always_matches_its_lines(db_session):
    data = ExpenseClaimCreate(
        claim_type="EXP", submission_date=date(2026, 9, 10), currency="CAD",
        line_items=[
            LineItemCreate(**_line(1, description="Hotel", net="100.00", tax="13.00",
                                   total="113.00")),
            LineItemCreate(**_line(2, description="Taxi", net="40.00", tax="5.20",
                                   total="45.20")),
        ],
    )
    claim = await expense_crud.create_claim(
        db_session, data, user_id=uuid.uuid4(), user_name="Emp",
        department_id=None, department_name="Ops")

    assert claim.total_amount == claim.net_amount + claim.tax_amount == Decimal("158.20")


# ── TRV meal per-diem on edit ─────────────────────────────────────────────────

async def _trv(db_session, *, description: str, net: str, tax: str, total: str):
    """A TRV built through the CRUD layer — the API's approved-TRA gate is a
    separate concern, covered by test_trv_travel_application_gate.py."""
    data = ExpenseClaimCreate(
        claim_type="TRV", submission_date=date(2026, 9, 10), currency="CAD",
        line_items=[LineItemCreate(**_line(1, description=description, net=net,
                                           tax=tax, total=total))],
    )
    return await expense_crud.create_claim(
        db_session, data, user_id=uuid.uuid4(), user_name="Emp",
        department_id=None, department_name="Ops")


async def test_a_compliant_trv_is_not_flagged(db_session):
    # Lunch cap is 23.00; 18.00 is inside it.
    claim = await _trv(db_session, description="Lunch, day 1",
                       net="18.00", tax="2.34", total="20.34")
    assert claim.is_over_budget is False


async def test_an_over_limit_trv_is_flagged_on_create(db_session):
    claim = await _trv(db_session, description="Lunch with supplier",
                       net="80.00", tax="10.40", total="90.40")
    assert claim.is_over_budget is True


async def test_editing_a_trv_over_the_cap_now_flags_it(db_session):
    """The regression: compliant on create, over-limit after the edit."""
    claim = await _trv(db_session, description="Lunch, day 1",
                       net="18.00", tax="2.34", total="20.34")
    assert claim.is_over_budget is False
    claim.status = "returned"                    # what an approver's Return does
    await db_session.flush()

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(
        line_items=[LineItemCreate(**_line(1, description="Lunch, day 1",
                                           net="80.00", tax="10.40", total="90.40"))],
    ))

    assert claim.is_over_budget is True


async def test_editing_a_trv_back_under_the_cap_clears_the_flag(db_session):
    claim = await _trv(db_session, description="Dinner, day 2",
                       net="120.00", tax="15.60", total="135.60")
    assert claim.is_over_budget is True
    claim.status = "returned"
    await db_session.flush()

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(
        line_items=[LineItemCreate(**_line(1, description="Dinner, day 2",
                                           net="40.00", tax="5.20", total="45.20"))],
    ))

    assert claim.is_over_budget is False


async def test_editing_an_exp_does_not_acquire_a_meal_flag(db_session):
    """Meal per-diems are a TRV rule; an EXP mentioning lunch is not over budget."""
    data = ExpenseClaimCreate(
        claim_type="EXP", submission_date=date(2026, 9, 10), currency="CAD",
        line_items=[LineItemCreate(**_line(1, description="Team lunch catering",
                                           net="400.00", tax="52.00", total="452.00"))],
    )
    claim = await expense_crud.create_claim(
        db_session, data, user_id=uuid.uuid4(), user_name="Emp",
        department_id=None, department_name="Ops")

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(
        line_items=[LineItemCreate(**_line(1, description="Team lunch catering",
                                           net="500.00", tax="65.00", total="565.00"))],
    ))

    assert claim.is_over_budget is False
