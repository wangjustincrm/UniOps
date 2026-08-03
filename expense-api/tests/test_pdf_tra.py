import uuid
from datetime import date
from app.models.expense import ExpenseClaim, ExpenseTraveler
from app.services.pdf_tra import build_travel_application_pdf


def test_build_travel_application_pdf_returns_pdf_bytes():
    claim = ExpenseClaim(
        id=uuid.uuid4(), claim_number="TRA-20260803-0001", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="Alice", department_name="Operations",
        submission_date=date(2026, 8, 3), travel_destination="Toronto",
        travel_from_date=date(2026, 8, 4), travel_to_date=date(2026, 8, 6),
        purpose="Supplier audit", notes="Driver Sam (external) accompanies",
        transport_modes=["airplane", "accommodation", "meal"],
        leave_from_date=date(2026, 8, 4), leave_to_date=date(2026, 8, 6),
        status="approved", created_by=uuid.uuid4())
    claim.travelers = [ExpenseTraveler(user_id=uuid.uuid4(), user_name="Alice", seq=0),
                       ExpenseTraveler(user_id=uuid.uuid4(), user_name="Bob", seq=1)]
    claim.approval_events = []
    data = build_travel_application_pdf(claim)
    assert isinstance(data, (bytes, bytearray))
    assert data[:4] == b"%PDF"
    assert len(data) > 800
