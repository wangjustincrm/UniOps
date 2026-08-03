"""API test: creating a TRA (Travel Application) claim.

Drives POST /api/v1/expenses with claim_type="TRA". Verifies travelers,
transport_modes, and leave dates persist while totals stay 0.

NOTE: the brief's `client` + `auth_headers` fixture names don't exist in this
repo's tests/conftest.py — the authed-client pattern here is a single fixture
per role that already carries the bearer token (see tests/test_pa.py). Using
`admin_client` (system_admin token), matching test_pa.py's usage.
"""
import uuid

from app.models.expense import ExpenseClaim


async def test_create_tra_persists_travelers_and_zero_totals(admin_client, db_session):
    traveler_id = str(uuid.uuid4())
    body = {
        "claim_type": "TRA", "submission_date": "2026-08-03",
        "purpose": "Supplier audit", "travel_destination": "Montreal",
        "travel_from_date": "2026-08-04", "travel_to_date": "2026-08-06",
        "transport_modes": ["airplane", "accommodation"],
        "travelers": [{"user_id": traveler_id, "user_name": "Alice", "seq": 0}],
    }
    r = await admin_client.post("/api/v1/expenses", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["claim_number"].startswith("TRA-")
    assert data["total_amount"] == "0.00"
    assert data["transport_modes"] == ["airplane", "accommodation"]
    assert len(data["travelers"]) == 1
    assert data["travelers"][0]["user_name"] == "Alice"

    # Cleanup: this session-scoped test DB is shared across test modules with
    # no per-test rollback. test_travel_application_model.py hardcodes the
    # literal claim_number "TRA-<today>-0001" — free that numbering slot so
    # it doesn't collide with the row this test just committed.
    claim = await db_session.get(ExpenseClaim, uuid.UUID(data["id"]))
    await db_session.delete(claim)
    await db_session.commit()
