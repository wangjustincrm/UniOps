"""TRV date-range lines: N days x a daily rate, optional invoice cap, country tax.

Business rule 2026-10-16: long overseas trips (2-3 months) cannot be claimed
one day at a time. A line may cover a range; days claimed default to the
range and may be lowered but never raised past it; the daily rate is capped
by the policy meal/incidental limit (hard, unlike single-day lines, which are
flagged); an invoice is optional, but when one is given the line total may not
exceed it. Abroad, lines carry no Canadian sales tax; a TRV of only range
lines may be submitted without an attachment.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import expenses as expenses_api

from app.crud import expense as expense_crud
from app.schemas.expense import ExpenseClaimCreate, ExpenseClaimUpdate, LineItemCreate
from tests.conftest import _client, _make_token

_ACCOUNT = dict(
    budget_account_id=uuid.uuid4(),
    budget_account_code="GA00101",
    budget_account_name="Travel & Entertainment",
)

TRIP_FROM, TRIP_TO = date(2026, 5, 21), date(2026, 8, 20)   # 92 days


def _range(n=1, *, desc="[Meals-Breakfast] per diem", frm=TRIP_FROM, to=TRIP_TO,
           days=92, rate="6.00", tax_rate="0", invoice=None) -> dict:
    net = Decimal(rate) * days
    tax = (net * Decimal(tax_rate)).quantize(Decimal("0.01"))
    return dict(
        line_number=n, expense_date=frm, description=desc,
        period_from=frm, period_to=to, period_days=days, daily_rate=Decimal(rate),
        net_amount=net, tax_amount=tax, total_amount=net + tax,
        invoice_amount=Decimal(invoice) if invoice is not None else None,
        **_ACCOUNT,
    )


def _trv(lines, *, country="US", currency="CAD") -> ExpenseClaimCreate:
    return ExpenseClaimCreate(
        claim_type="TRV", submission_date=date(2026, 8, 25), currency=currency,
        travel_from_date=TRIP_FROM, travel_to_date=TRIP_TO,
        travel_destination="Shanghai", travel_country=country, purpose="Plant start-up",
        line_items=[LineItemCreate(**li) for li in lines],
    )


async def _create(db, data):
    return await expense_crud.create_claim(
        db, data=data, user_id=uuid.uuid4(), user_name="Long Trip",
        department_id=None, department_name="Ops")


# ── Schema: shape of a range line ─────────────────────────────────────────────

def test_a_range_line_is_days_times_rate():
    li = LineItemCreate(**_range(days=92, rate="6.00"))
    assert li.net_amount == Decimal("552.00")


def test_days_may_be_lowered_below_the_range():
    li = LineItemCreate(**_range(days=20, rate="100.00", desc="[Hotel] serviced apt"))
    assert li.total_amount == Decimal("2000.00")


def test_days_may_not_exceed_the_range():
    with pytest.raises(ValidationError, match="between 1 and 92"):
        LineItemCreate(**_range(days=93))


def test_net_that_is_not_days_times_rate_is_refused():
    bad = _range(days=10, rate="6.00")
    bad.update(net_amount=Decimal("600.00"), total_amount=Decimal("600.00"))
    with pytest.raises(ValidationError, match="10 days x 6.00"):
        LineItemCreate(**bad)


def test_a_partial_range_is_refused():
    bad = _range()
    bad["daily_rate"] = None
    with pytest.raises(ValidationError, match="needs from, to, days and daily rate"):
        LineItemCreate(**bad)


def test_total_above_the_invoice_is_refused():
    with pytest.raises(ValidationError, match="exceeds the invoice"):
        LineItemCreate(**_range(days=20, rate="100.00", desc="[Hotel] apt", invoice="1999.99"))


def test_total_equal_to_the_invoice_is_accepted():
    li = LineItemCreate(**_range(days=20, rate="100.00", desc="[Hotel] apt", invoice="2000.00"))
    assert li.invoice_amount == Decimal("2000.00")


def test_single_day_lines_are_unchanged():
    li = LineItemCreate(
        line_number=1, expense_date=date(2026, 9, 10), description="[Transportation] taxi",
        net_amount=Decimal("40"), tax_amount=Decimal("5.20"), total_amount=Decimal("45.20"),
        **_ACCOUNT)
    assert li.period_from is None and li.invoice_amount is None


# ── crud: policy cap, trip dates, country tax ────────────────────────────────

async def test_range_rate_within_the_meal_limit_is_saved(db_session):
    claim = await _create(db_session, _trv([_range(rate="6.00")]))
    assert claim.total_amount == Decimal("552.00")
    assert claim.line_items[0].period_days == 92
    assert claim.travel_country == "US"
    assert claim.is_over_budget is False


async def test_range_rate_above_the_meal_limit_is_refused(db_session):
    """Breakfast default cap is 23.00 — a range line is hard-capped, not flagged."""
    with pytest.raises(ValueError, match="exceeds the breakfast limit"):
        await _create(db_session, _trv([_range(rate="23.01")]))


async def test_hotel_range_has_no_policy_cap(db_session):
    claim = await _create(db_session, _trv([_range(desc="[Hotel] apt", days=20, rate="250.00")]))
    assert claim.total_amount == Decimal("5000.00")


async def test_range_outside_the_trip_dates_is_refused(db_session):
    with pytest.raises(ValueError, match="outside the trip dates"):
        await _create(db_session, _trv([_range(frm=date(2026, 5, 20), days=10)]))


async def test_abroad_lines_may_not_carry_tax(db_session):
    with pytest.raises(ValueError, match="no sales tax"):
        await _create(db_session, _trv([_range(tax_rate="0.13")], country="US"))


async def test_abroad_lines_may_not_carry_a_tax_code(db_session):
    line = _range()
    line["tax_code"] = "HST_ON"
    with pytest.raises(ValueError, match="no sales tax"):
        await _create(db_session, _trv([line], country="US"))


async def test_trv_in_a_foreign_currency_is_refused(db_session):
    with pytest.raises(ValueError, match="CAD only"):
        await _create(db_session, _trv([_range()], currency="USD"))


async def test_edit_cannot_switch_a_trv_to_usd(db_session):
    claim = await _create(db_session, _trv([_range()]))
    with pytest.raises(ValueError, match="CAD only"):
        await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(currency="USD"))


async def test_canada_line_keeps_its_picked_tax_code(db_session):
    """GST 5% (e.g. Alberta) picked on the line is stored as sent."""
    line = _range(rate="10.00", days=10, tax_rate="0.05", to=date(2026, 5, 30))
    line["tax_code"] = "GST"
    claim = await _create(db_session, _trv([line], country="CA"))
    assert claim.line_items[0].tax_code == "GST"
    assert claim.tax_amount == Decimal("5.00")


async def test_canada_lines_keep_hst(db_session):
    claim = await _create(db_session, _trv([_range(rate="10.00", days=10, tax_rate="0.13",
                                                   to=date(2026, 5, 30))], country="CA"))
    assert claim.tax_amount == Decimal("13.00")


async def test_edit_that_shrinks_the_trip_rechecks_the_periods(db_session):
    """Moving only the header dates must still refuse a period that now falls outside."""
    claim = await _create(db_session, _trv([_range()]))
    with pytest.raises(ValueError, match="outside the trip dates"):
        await expense_crud.update_claim(
            db_session, claim, ExpenseClaimUpdate(travel_to_date=date(2026, 7, 31)))


async def test_edit_cannot_raise_a_range_rate_past_the_limit(db_session):
    claim = await _create(db_session, _trv([_range()]))
    with pytest.raises(ValueError, match="exceeds the breakfast limit"):
        await expense_crud.update_claim(
            db_session, claim,
            ExpenseClaimUpdate(line_items=[LineItemCreate(**_range(rate="30.00"))]))


async def test_bad_country_code_is_refused(db_session):
    with pytest.raises(ValueError, match="two-letter"):
        await _create(db_session, _trv([_range()], country="usa"))


# ── submit gate: all-range TRV needs no attachment ───────────────────────────

async def _committed_trv(test_engine, lines) -> tuple[uuid.UUID, uuid.UUID]:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    owner = uuid.uuid4()
    async with factory() as s:
        claim = await expense_crud.create_claim(
            s, data=_trv(lines), user_id=owner, user_name="Long Trip",
            department_id=None, department_name="Ops")
        await s.commit()
        return claim.id, owner


@pytest.fixture
def engine_called(monkeypatch):
    """Stand in for approval-api: record that the gate let the submit through."""
    calls = []

    async def fake_delegate(*args, **kwargs):
        calls.append(kwargs or args)
        raise HTTPException(status_code=418, detail="reached approval engine")

    monkeypatch.setattr(expenses_api, "delegate_action", fake_delegate)
    return calls


async def test_all_range_trv_submits_without_an_attachment(test_engine, engine_called):
    cid, owner = await _committed_trv(test_engine, [_range()])
    async with _client(_make_token("requester", str(owner))) as c:
        r = await c.post(f"/api/v1/expenses/{cid}/action", json={"action": "submit"})
    assert "attachment is required" not in r.text
    assert engine_called, r.text


async def test_trv_with_a_single_day_line_still_needs_an_attachment(test_engine, engine_called):
    day = dict(line_number=2, expense_date=date(2026, 6, 1), description="[Transportation] taxi",
               net_amount=Decimal("40"), tax_amount=Decimal("0"), total_amount=Decimal("40"),
               **_ACCOUNT)
    cid, owner = await _committed_trv(test_engine, [_range(), day])
    async with _client(_make_token("requester", str(owner))) as c:
        r = await c.post(f"/api/v1/expenses/{cid}/action", json={"action": "submit"})
    assert r.status_code == 409 and "attachment is required" in r.text
    assert not engine_called
