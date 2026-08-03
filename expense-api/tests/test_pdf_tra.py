import uuid
from datetime import date
from app.models.expense import ExpenseClaim, ExpenseTraveler
from app.services.pdf_tra import build_travel_application_pdf


def _claim():
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
    return claim


def test_build_travel_application_pdf_returns_pdf_bytes():
    """No approvals passed → still a valid PDF with blank signature lines."""
    claim = _claim()
    data = build_travel_application_pdf(claim)
    assert isinstance(data, (bytes, bytearray))
    assert data[:4] == b"%PDF"
    assert len(data) > 800


def test_build_travel_application_pdf_populates_signatures_from_approvals():
    """Passing explicit `approvals` (the real, DB-resolved source — see task-8
    fix report) must exercise the populated signature-row path, not silently
    fall back to blank "________" lines.
    """
    claim = _claim()
    approvals = [
        {"step_idx": 0, "actor_name": "Alice Head", "acted_date": "2026-08-05"},
        {"step_idx": 1, "actor_name": "Finance Fred", "acted_date": "2026-08-06"},
        {"step_idx": 2, "actor_name": "GM Grace", "acted_date": "2026-08-07"},
    ]
    data = build_travel_application_pdf(claim, approvals=approvals)
    assert isinstance(data, (bytes, bytearray))
    assert data[:4] == b"%PDF"
    assert len(data) > 800

    # The populated build must differ from the blank-signature build (proves the
    # approvals path actually changes the rendered content instead of being dead).
    blank = build_travel_application_pdf(claim, approvals=None)
    assert data != blank


def test_build_travel_application_pdf_ignores_approval_missing_step_idx():
    """A malformed approval dict (no step_idx) must not crash PDF build — it's
    simply skipped, leaving that signature row blank (task-8 fix: fail-safe
    step_idx access)."""
    claim = _claim()
    data = build_travel_application_pdf(claim, approvals=[{"actor_name": "No Step"}])
    assert data[:4] == b"%PDF"
    assert len(data) > 800
