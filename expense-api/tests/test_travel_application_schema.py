"""Tests for TRA (Travel Application) schema."""
import uuid
from datetime import date
from app.schemas.expense import ExpenseClaimCreate, ExpenseClaimResponse


def test_tra_create_schema_accepts_travelers_and_transport():
    payload = ExpenseClaimCreate(
        claim_type="TRA", submission_date=date(2026, 8, 3),
        purpose="Client visit", notes="Driver Sam (external) tags along",
        travel_destination="Toronto",
        travel_from_date=date(2026, 8, 4), travel_to_date=date(2026, 8, 6),
        transport_modes=["airplane", "meal"],
        leave_from_date=date(2026, 8, 4), leave_to_date=date(2026, 8, 6),
        travelers=[{"user_id": uuid.uuid4(), "user_name": "Alice", "seq": 0}],
    )
    assert payload.transport_modes == ["airplane", "meal"]
    assert payload.travelers[0].user_name == "Alice"


def test_trv_create_schema_accepts_travel_application_id():
    payload = ExpenseClaimCreate(
        claim_type="TRV", submission_date=date(2026, 8, 7),
        travel_application_id=uuid.uuid4(), line_items=[],
    )
    assert payload.travel_application_id is not None
