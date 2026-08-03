import uuid
from datetime import date
import pytest
from sqlalchemy import select
from app.models.expense import ExpenseClaim, ExpenseTraveler


async def test_tra_claim_persists_travelers_and_transport(db_session):
    claim = ExpenseClaim(
        claim_number="TRA-20260803-0001", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="Alice",
        department_name="Ops", submission_date=date(2026, 8, 3),
        transport_modes=["airplane", "accommodation"],
        leave_from_date=date(2026, 8, 4), leave_to_date=date(2026, 8, 6),
        status="draft", created_by=uuid.uuid4(),
    )
    db_session.add(claim)
    await db_session.flush()
    db_session.add(ExpenseTraveler(
        claim_id=claim.id, user_id=uuid.uuid4(), user_name="Bob", seq=0))
    await db_session.flush()
    await db_session.refresh(claim, ["travelers"])
    assert claim.transport_modes == ["airplane", "accommodation"]
    assert len(claim.travelers) == 1
    assert claim.travelers[0].user_name == "Bob"
