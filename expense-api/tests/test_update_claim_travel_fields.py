"""update_claim must persist every field ExpenseClaimUpdate accepts.

The scalar loop stopped after vehicle_owned_by, so the travel fields — which
the schema takes and the client sends — were parsed, validated and then thrown
away, with the save reporting success. `travelers` was accepted and ignored the
same way. That matters most exactly where editing matters: a TRV or TRA an
approver returned for a wrong date or a wrong traveller could not be corrected.
"""
import uuid
from datetime import date
from decimal import Decimal

from app.crud import expense as expense_crud
from app.schemas.expense import (
    ExpenseClaimCreate, ExpenseClaimUpdate, LineItemCreate, TravelerCreate,
)

_ACCOUNT = dict(
    budget_account_id=uuid.uuid4(),
    budget_account_code="GA00101",
    budget_account_name="Travel & Entertainment",
)


def _line(n=1, *, net="100.00", tax="13.00", total="113.00"):
    return LineItemCreate(
        line_number=n, expense_date=date(2026, 9, 10), description="Hotel",
        net_amount=Decimal(net), tax_amount=Decimal(tax), total_amount=Decimal(total),
        **_ACCOUNT,
    )


async def _create(db_session, **kwargs):
    data = ExpenseClaimCreate(submission_date=date(2026, 9, 10), currency="CAD", **kwargs)
    return await expense_crud.create_claim(
        db_session, data, user_id=uuid.uuid4(), user_name="Emp",
        department_id=None, department_name="Ops")


# ── TRV travel fields ─────────────────────────────────────────────────────────

async def test_travel_dates_and_destination_are_saved(db_session):
    claim = await _create(
        db_session, claim_type="TRV", line_items=[_line()],
        travel_from_date=date(2026, 10, 1), travel_to_date=date(2026, 10, 5),
        travel_destination="Montreal",
    )

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(
        travel_from_date=date(2026, 10, 2),
        travel_to_date=date(2026, 10, 8),
        travel_destination="Quebec City",
    ))

    assert claim.travel_from_date == date(2026, 10, 2)
    assert claim.travel_to_date == date(2026, 10, 8)
    assert claim.travel_destination == "Quebec City"


async def test_the_referenced_travel_application_can_be_corrected(db_session):
    tra = await _create(db_session, claim_type="TRA", travelers=[])
    other = await _create(db_session, claim_type="TRA", travelers=[])
    claim = await _create(db_session, claim_type="TRV", line_items=[_line()],
                          travel_application_id=tra.id)

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(
        travel_application_id=other.id))

    assert claim.travel_application_id == other.id


# ── TRA roster and trip shape ─────────────────────────────────────────────────

async def test_the_traveller_roster_can_be_rewritten(db_session):
    keep, drop, add = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    claim = await _create(db_session, claim_type="TRA", travelers=[
        TravelerCreate(user_id=keep, user_name="Keep Me", seq=0),
        TravelerCreate(user_id=drop, user_name="Drop Me", seq=1),
    ])
    assert {t.user_id for t in claim.travelers} == {keep, drop}

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(travelers=[
        TravelerCreate(user_id=keep, user_name="Keep Me", seq=0),
        TravelerCreate(user_id=add, user_name="Add Me", seq=1),
    ]))

    assert {t.user_id for t in claim.travelers} == {keep, add}


async def test_emptying_the_roster_is_a_real_edit(db_session):
    """An empty list means "I removed everyone", not "leave it alone"."""
    claim = await _create(db_session, claim_type="TRA", travelers=[
        TravelerCreate(user_id=uuid.uuid4(), user_name="Someone", seq=0),
    ])

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(travelers=[]))

    assert claim.travelers == []


async def test_transport_modes_and_leave_dates_are_saved(db_session):
    claim = await _create(db_session, claim_type="TRA", travelers=[],
                          transport_modes=["flight"],
                          leave_from_date=date(2026, 10, 1),
                          leave_to_date=date(2026, 10, 3))

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(
        transport_modes=["flight", "car"],
        leave_from_date=date(2026, 10, 2),
        leave_to_date=date(2026, 10, 6),
    ))

    assert claim.transport_modes == ["flight", "car"]
    assert claim.leave_from_date == date(2026, 10, 2)
    assert claim.leave_to_date == date(2026, 10, 6)


# ── unchanged behaviour ───────────────────────────────────────────────────────

async def test_omitted_fields_are_left_alone(db_session):
    claim = await _create(db_session, claim_type="TRV", line_items=[_line()],
                          travel_destination="Montreal", purpose="Audit visit")

    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(notes="Added a note"))

    assert claim.notes == "Added a note"
    assert claim.travel_destination == "Montreal"
    assert claim.purpose == "Audit visit"


async def test_an_approved_claim_still_cannot_be_edited(db_session):
    import pytest
    claim = await _create(db_session, claim_type="TRV", line_items=[_line()],
                          travel_destination="Montreal")
    claim.status = "approved"
    await db_session.flush()

    with pytest.raises(ValueError, match="draft or returned"):
        await expense_crud.update_claim(db_session, claim,
                                        ExpenseClaimUpdate(travel_destination="Anywhere"))
    assert claim.travel_destination == "Montreal"
