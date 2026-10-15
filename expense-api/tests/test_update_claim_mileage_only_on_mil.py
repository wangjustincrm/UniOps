"""Only a MIL claim has mileage (user, 2026-10-14).

The OA form saves a general expense with `trip_items: []`. update_claim ran the
line-item branch and then the trip branch: the empty trip list recomputed the
claim as mileage — total 0.00, total_km 0 — over the 423.11 the lines had just
produced. EXP-20260929-0001 sat in production at 0.00 with "Total km 0.0 km".
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.crud import expense as expense_crud
from app.schemas.expense import (
    ExpenseClaimCreate, ExpenseClaimUpdate, LineItemCreate, TripItemCreate,
)

_ACCOUNT = dict(budget_account_id=uuid.uuid4(), budget_account_code="CRM00303",
                budget_account_name="Software Fees")


def _line(n, net, tax, total):
    return LineItemCreate(line_number=n, expense_date=date(2026, 9, 25), description="Licence",
                          net_amount=Decimal(net), tax_amount=Decimal(tax),
                          total_amount=Decimal(total), **_ACCOUNT)


def _trip(n, km):
    return TripItemCreate(trip_number=n, trip_date=date(2026, 9, 10), from_location="Plant",
                          to_location="Toronto", purpose="Visit", distance_km=Decimal(km),
                          rate_per_km=Decimal("0.70"), amount=Decimal("0"))


async def _create(db_session, **kwargs):
    data = ExpenseClaimCreate(submission_date=date(2026, 9, 29), currency="CAD", **kwargs)
    return await expense_crud.create_claim(db_session, data, user_id=uuid.uuid4(), user_name="Emp",
                                           department_id=None, department_name="GMO")


LINES = [("140.00", "18.20", "158.20"), ("139.43", "18.13", "157.56"), ("95.00", "12.35", "107.35")]


@pytest.mark.parametrize("claim_type", ["EXP", "TRV"])
async def test_editing_a_receipt_claim_with_an_empty_trip_list_keeps_its_totals(db_session, claim_type):
    claim = await _create(db_session, claim_type=claim_type, line_items=[_line(1, *LINES[0])])

    # Exactly what the OA form sends on save.
    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(
        line_items=[_line(i + 1, *v) for i, v in enumerate(LINES)], trip_items=[]))

    assert claim.total_amount == Decimal("423.11")
    assert claim.net_amount == Decimal("374.43")
    assert claim.total_km is None


async def test_trip_items_sent_with_a_receipt_claim_are_ignored(db_session):
    claim = await _create(db_session, claim_type="EXP", line_items=[_line(1, *LINES[0])])

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(trip_items=[_trip(1, 100)]))

    assert claim.total_amount == Decimal("158.20")
    assert claim.total_km is None
    assert list(claim.trip_items) == []


async def test_editing_a_receipt_claim_clears_a_stale_total_km(db_session):
    """The production row already carries total_km = 0 from the bug; saving it
    again must take that away, or 'Total km' keeps showing."""
    claim = await _create(db_session, claim_type="EXP", line_items=[_line(1, *LINES[0])])
    claim.total_km = Decimal("0")

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(
        line_items=[_line(i + 1, *v) for i, v in enumerate(LINES)], trip_items=[]))

    assert claim.total_km is None and claim.total_amount == Decimal("423.11")


async def test_mileage_claim_still_computes_from_trips(db_session):
    claim = await _create(db_session, claim_type="MIL", trip_items=[_trip(1, 10)])

    # A MIL save may carry an empty line list; it must not zero the mileage.
    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(
        trip_items=[_trip(1, 100), _trip(2, 50)], line_items=[]))

    assert claim.total_km == Decimal("150")
    assert claim.total_amount == Decimal("105.00")
