"""Training records, certifications, and the gap report.

The gap report is normally the first thing a Ministry inspector asks for, so
these cases cover the two ways someone can be non-compliant — never took it,
and took it but it lapsed — because those need different action.
"""
import uuid
from datetime import date, timedelta

import pytest
import sqlalchemy

from app.models.training import Course
from app.models.worker import JobPosition, PositionRequirement, WorkerPosition

TODAY = date.today()


@pytest.fixture
async def courses(db_session):
    """Two of the eight statutory courses: one for everyone, one by position."""
    whmis = Course(id=uuid.uuid4(), code="WHMIS", name="WHMIS", is_statutory=True,
                   applies_to_all=True, validity_months=12)
    forklift = Course(id=uuid.uuid4(), code="FORKLIFT", name="Forklift", is_statutory=True,
                      applies_to_all=False, validity_months=36)
    db_session.add_all([whmis, forklift])
    await db_session.flush()
    return {"whmis": whmis, "forklift": forklift}


# ── Recording ───────────────────────────────────────────────────────────────

async def test_recording_training_derives_the_expiry_from_the_course(
    hse_manager, worker, courses
):
    _, client = hse_manager
    learner, _ = worker
    body = (await client.post("/api/v1/training/records", json={
        "user_id": str(learner.id), "course_id": str(courses["whmis"].id),
        "completed_on": TODAY.isoformat(),
    })).json()
    assert body["course_label"] == "WHMIS"
    assert body["user_name"] == learner.full_name
    # 12 months of validity, so roughly a year out — not open-ended.
    assert body["expires_on"] is not None
    assert date.fromisoformat(body["expires_on"]) > TODAY


async def test_an_explicit_expiry_wins_over_the_derived_one(hse_manager, worker, courses):
    """The certificate on the desk is the authority, not our arithmetic."""
    _, client = hse_manager
    learner, _ = worker
    stated = (TODAY + timedelta(days=200)).isoformat()
    body = (await client.post("/api/v1/training/records", json={
        "user_id": str(learner.id), "course_id": str(courses["whmis"].id),
        "completed_on": TODAY.isoformat(), "expires_on": stated,
    })).json()
    assert body["expires_on"] == stated


async def test_an_expiry_before_completion_is_refused(hse_manager, worker, courses):
    _, client = hse_manager
    learner, _ = worker
    r = await client.post("/api/v1/training/records", json={
        "user_id": str(learner.id), "course_id": str(courses["whmis"].id),
        "completed_on": TODAY.isoformat(),
        "expires_on": (TODAY - timedelta(days=1)).isoformat(),
    })
    assert r.status_code == 422


async def test_a_worker_cannot_record_their_own_training(worker, courses):
    learner, client = worker
    r = await client.post("/api/v1/training/records", json={
        "user_id": str(learner.id), "course_id": str(courses["whmis"].id),
        "completed_on": TODAY.isoformat(),
    })
    assert r.status_code == 403


# ── Certifications and their clocks ──────────────────────────────────────────

async def test_a_certification_with_an_expiry_starts_a_clock(hse_manager, worker, db_session):
    """Certificate expiries are rows in the same deadline table as the
    Ministry's 48 hours, so the same sweep chases them."""
    _, client = hse_manager
    holder, _ = worker
    expiry = TODAY + timedelta(days=45)
    cert = (await client.post("/api/v1/training/certifications", json={
        "user_id": str(holder.id), "cert_type_label": "Lift truck licence",
        "issued_on": TODAY.isoformat(), "expires_on": expiry.isoformat(),
        "is_blocking": True,
    })).json()
    assert cert["days_until_expiry"] == 45
    assert cert["is_expired"] is False

    row = (await db_session.execute(sqlalchemy.text(
        "SELECT kind, clock_type FROM ehs_statutory_deadlines"
        " WHERE source_type = 'cert' AND source_id = :i"), {"i": cert["id"]})).mappings().one()
    assert row["kind"] == "cert_expiry"
    assert row["clock_type"] == "calendar"


async def test_a_certification_without_an_expiry_starts_no_clock(hse_manager, worker, db_session):
    _, client = hse_manager
    holder, _ = worker
    cert = (await client.post("/api/v1/training/certifications", json={
        "user_id": str(holder.id), "cert_type_label": "Confined space entry",
    })).json()
    assert cert["days_until_expiry"] is None
    count = (await db_session.execute(sqlalchemy.text(
        "SELECT count(*) FROM ehs_statutory_deadlines WHERE source_id = :i"),
        {"i": cert["id"]})).scalar()
    assert count == 0


async def test_an_expired_certification_says_so(hse_manager, worker):
    _, client = hse_manager
    holder, _ = worker
    cert = (await client.post("/api/v1/training/certifications", json={
        "user_id": str(holder.id), "cert_type_label": "Lift truck licence",
        "expires_on": (TODAY - timedelta(days=3)).isoformat(),
    })).json()
    assert cert["is_expired"] is True
    assert cert["days_until_expiry"] == -3


async def test_renewing_pushes_the_clock_out_and_resets_escalation(
    hse_manager, worker, db_session
):
    _, client = hse_manager
    holder, _ = worker
    cert = (await client.post("/api/v1/training/certifications", json={
        "user_id": str(holder.id), "cert_type_label": "Lift truck licence",
        "expires_on": (TODAY + timedelta(days=5)).isoformat(),
    })).json()
    # Pretend the sweep has already warned about it.
    await db_session.execute(sqlalchemy.text(
        "UPDATE ehs_statutory_deadlines SET escalation_level = 2 WHERE source_id = :i"),
        {"i": cert["id"]})
    await db_session.flush()

    new_expiry = TODAY + timedelta(days=1100)
    renewed = (await client.post(f"/api/v1/training/certifications/{cert['id']}/renew",
                                 json={"expires_on": new_expiry.isoformat()})).json()
    assert renewed["expires_on"] == new_expiry.isoformat()

    row = (await db_session.execute(sqlalchemy.text(
        "SELECT escalation_level FROM ehs_statutory_deadlines WHERE source_id = :i"),
        {"i": cert["id"]})).scalar()
    assert row == 0, "renewing must reset the escalation, not leave it warning forever"


async def test_renewal_cannot_shorten_a_certification(hse_manager, worker):
    _, client = hse_manager
    holder, _ = worker
    cert = (await client.post("/api/v1/training/certifications", json={
        "user_id": str(holder.id), "cert_type_label": "Lift truck licence",
        "expires_on": (TODAY + timedelta(days=100)).isoformat(),
    })).json()
    r = await client.post(f"/api/v1/training/certifications/{cert['id']}/renew",
                          json={"expires_on": (TODAY + timedelta(days=10)).isoformat()})
    assert r.status_code == 422


async def test_expiring_soon_filter(hse_manager, worker, test_engine):
    from tests.conftest import make_user
    _, client = hse_manager
    soon_holder, _ = worker
    later_holder = await make_user(test_engine, role="worker")

    soon = (await client.post("/api/v1/training/certifications", json={
        "user_id": str(soon_holder.id), "cert_type_label": "Expiring soon",
        "expires_on": (TODAY + timedelta(days=20)).isoformat()})).json()
    later = (await client.post("/api/v1/training/certifications", json={
        "user_id": str(later_holder.id), "cert_type_label": "Expiring later",
        "expires_on": (TODAY + timedelta(days=200)).isoformat()})).json()

    rows = (await client.get("/api/v1/training/certifications",
                             params={"expiring_within_days": 30})).json()
    ids = {r["id"] for r in rows}
    assert soon["id"] in ids
    assert later["id"] not in ids


# ── The gap report ──────────────────────────────────────────────────────────

async def test_a_universal_course_is_required_of_everyone(hse_manager, worker, courses):
    _, client = hse_manager
    report = (await client.get("/api/v1/training/gaps")).json()
    gaps = [g for g in report["gaps"] if g["course_code"] == "WHMIS"]
    assert gaps, "WHMIS applies to all staff, so everyone untrained is a gap"
    assert all(g["reason"] == "missing" for g in gaps)


async def test_taking_the_course_closes_the_gap(hse_manager, worker, courses):
    _, client = hse_manager
    learner, _ = worker
    before = (await client.get("/api/v1/training/gaps")).json()
    assert any(g["user_id"] == str(learner.id) and g["course_code"] == "WHMIS"
               for g in before["gaps"])

    await client.post("/api/v1/training/records", json={
        "user_id": str(learner.id), "course_id": str(courses["whmis"].id),
        "completed_on": TODAY.isoformat()})

    after = (await client.get("/api/v1/training/gaps")).json()
    assert not any(g["user_id"] == str(learner.id) and g["course_code"] == "WHMIS"
                   for g in after["gaps"])


async def test_an_expired_record_is_a_gap_of_a_different_kind(hse_manager, worker, courses):
    """Never took it and took it but it lapsed need different action, so the
    report distinguishes them rather than reporting both as missing."""
    _, client = hse_manager
    learner, _ = worker
    await client.post("/api/v1/training/records", json={
        "user_id": str(learner.id), "course_id": str(courses["whmis"].id),
        "completed_on": (TODAY - timedelta(days=400)).isoformat(),
        "expires_on": (TODAY - timedelta(days=35)).isoformat()})

    report = (await client.get("/api/v1/training/gaps")).json()
    mine = [g for g in report["gaps"]
            if g["user_id"] == str(learner.id) and g["course_code"] == "WHMIS"]
    assert len(mine) == 1
    assert mine[0]["reason"] == "expired"
    assert mine[0]["expired_on"] == (TODAY - timedelta(days=35)).isoformat()


async def test_a_position_course_is_required_only_of_those_who_hold_it(
    hse_manager, worker, courses, db_session, test_engine
):
    from tests.conftest import make_user
    _, client = hse_manager
    driver, _ = worker
    non_driver = await make_user(test_engine, role="worker")

    position = JobPosition(id=uuid.uuid4(), code="LIFT", name="Lift truck operator")
    db_session.add(position)
    await db_session.flush()
    db_session.add_all([
        PositionRequirement(id=uuid.uuid4(), position_id=position.id,
                            requirement_type="training", course_id=courses["forklift"].id),
        WorkerPosition(id=uuid.uuid4(), user_id=driver.id, position_id=position.id,
                       effective_from=TODAY - timedelta(days=30)),
    ])
    await db_session.flush()

    report = (await client.get("/api/v1/training/gaps")).json()
    forklift_gaps = {g["user_id"] for g in report["gaps"] if g["course_code"] == "FORKLIFT"}
    assert str(driver.id) in forklift_gaps
    assert str(non_driver.id) not in forklift_gaps


async def test_a_position_held_in_the_past_no_longer_requires_its_training(
    hse_manager, worker, courses, db_session
):
    _, client = hse_manager
    former_driver, _ = worker
    position = JobPosition(id=uuid.uuid4(), code="LIFT", name="Lift truck operator")
    db_session.add(position)
    await db_session.flush()
    db_session.add_all([
        PositionRequirement(id=uuid.uuid4(), position_id=position.id,
                            requirement_type="training", course_id=courses["forklift"].id),
        WorkerPosition(id=uuid.uuid4(), user_id=former_driver.id, position_id=position.id,
                       effective_from=TODAY - timedelta(days=400),
                       effective_to=TODAY - timedelta(days=30)),
    ])
    await db_session.flush()

    report = (await client.get("/api/v1/training/gaps")).json()
    assert not any(g["user_id"] == str(former_driver.id) and g["course_code"] == "FORKLIFT"
                   for g in report["gaps"])


async def test_the_report_carries_a_compliance_rate(hse_manager, worker, courses):
    _, client = hse_manager
    report = (await client.get("/api/v1/training/gaps")).json()
    assert report["workers_considered"] >= 1
    assert 0.0 <= report["compliance_rate"] <= 1.0
    assert report["as_of"] == TODAY.isoformat()


async def test_an_auditor_can_read_the_gap_report(auditor, courses):
    _, client = auditor
    assert (await client.get("/api/v1/training/gaps")).status_code == 200


async def test_a_worker_cannot_read_the_gap_report(worker, courses):
    _, client = worker
    assert (await client.get("/api/v1/training/gaps")).status_code == 403
