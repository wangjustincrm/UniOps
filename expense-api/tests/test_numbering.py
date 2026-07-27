"""Regression: expense claim number allocation survives sequence gaps.

A renumbered/deleted claim leaves count() lagging the real max tail, so the old
count()+1 reissues an already-existing claim_number -> UniqueViolation. The shared
allocator keys off max tail + advisory lock instead.
"""
import uuid
from datetime import date

from sqlalchemy import text

from app.crud import expense as expense_crud
from app.schemas.expense import ExpenseClaimCreate


async def test_claim_number_survives_gap(db_session):
    data = ExpenseClaimCreate(claim_type="MIL", submission_date=date.today(), currency="CAD")
    args = dict(user_id=uuid.uuid4(), user_name="Emp", department_id=None, department_name="Ops")

    c1 = await expense_crud.create_claim(db_session, data, **args)
    c2 = await expense_crud.create_claim(db_session, data, **args)

    # Move c1 out of today's MIL-<today> window so count() lags the real max tail.
    await db_session.execute(
        text("UPDATE expense_claims SET claim_number = :n WHERE id = :i"),
        {"n": "MIL-19000101-0001", "i": c1.id},
    )
    await db_session.flush()

    # Under the old count()+1 this reissues c2's number -> UniqueViolation.
    c3 = await expense_crud.create_claim(db_session, data, **args)
    assert c3.claim_number != c2.claim_number, "reissued an existing claim number"
