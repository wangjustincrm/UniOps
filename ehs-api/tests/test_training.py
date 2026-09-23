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
    later_holder = await make_user(test_engine, role="requester")

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
    non_driver = await make_user(test_engine, role="requester")

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


async def test_a_course_everyone_has_taken_still_appears_at_nought(
    hse_manager, worker, courses, db_session
):
    """The reason the per-course counts are made here and not in the browser.

    A course nobody is behind on produces no gap rows, so a summary built from
    those rows would drop it — and the screen that course sessions are booked
    from would stop showing the one course the plant is on top of, which reads
    as the course having been withdrawn rather than completed.
    """
    from sqlalchemy import text
    _, client = hse_manager

    everyone = (await db_session.execute(
        text("SELECT id FROM users WHERE is_active"))).scalars().all()
    for user_id in everyone:
        r = await client.post("/api/v1/training/records", json={
            "user_id": str(user_id), "course_id": str(courses["whmis"].id),
            "completed_on": TODAY.isoformat(),
        })
        assert r.status_code in (200, 201), r.text

    report = (await client.get("/api/v1/training/gaps")).json()
    summary = {c["course_code"]: c for c in report["courses"]}

    assert "WHMIS" in summary, "a fully-trained course must still be reported"
    assert summary["WHMIS"]["missing"] == 0
    assert summary["WHMIS"]["expired"] == 0
    assert summary["WHMIS"]["required_of"] == len(everyone)
    assert not [g for g in report["gaps"] if g["course_code"] == "WHMIS"]


async def test_the_gap_report_says_which_department_to_chase(
    hse_manager, worker, courses, db_session
):
    """A name on its own does not tell a supervisor whether it is theirs."""
    from sqlalchemy import text
    _, client = hse_manager
    learner, _ = worker

    dept_id = (await db_session.execute(text(
        "INSERT INTO departments (id, code, name, is_active)"
        " VALUES (gen_random_uuid(), 'PKG', 'Packaging', true) RETURNING id"))).scalar()
    await db_session.execute(text("UPDATE users SET department_id = :d WHERE id = :u"),
                             {"d": dept_id, "u": learner.id})
    await db_session.flush()

    report = (await client.get("/api/v1/training/gaps")).json()
    theirs = [g for g in report["gaps"] if g["user_id"] == str(learner.id)]
    assert theirs, "this user has taken nothing, so they are behind on WHMIS"
    assert all(g["department_name"] == "Packaging" for g in theirs)


async def test_who_must_take_a_course_can_be_changed(hse_manager, worker, courses, db_session):
    """Four courses are required of every employee, and that was not editable.

    The compliance report counts every person in the plant against that list,
    so a list nobody can change is a decision taken once and enforced for ever.
    The first acceptance round asked to "remove the mandatory courses from all
    staff" and there was nothing to click.
    """
    _, client = hse_manager
    whmis = courses["whmis"]
    assert whmis.applies_to_all

    before = (await client.get("/api/v1/training/gaps")).json()
    assert any(c["course_code"] == "WHMIS" for c in before["courses"])

    r = await client.patch(f"/api/v1/training/courses/{whmis.id}",
                           json={"applies_to_all": False})
    assert r.status_code == 200, r.text
    assert r.json()["applies_to_all"] is False

    after = (await client.get("/api/v1/training/gaps")).json()
    assert not any(c["course_code"] == "WHMIS" for c in after["courses"]), (
        "it must stop being counted against everybody straight away")


async def test_whether_a_course_is_statutory_is_not_ours_to_change(hse_manager, courses):
    """That follows from the regulation, not from us."""
    _, client = hse_manager
    r = await client.patch(f"/api/v1/training/courses/{courses['whmis'].id}",
                           json={"is_statutory": False})
    assert r.status_code == 422


async def test_taking_somebody_off_a_position_and_putting_them_back(hse_manager, worker, db_session):
    """Removing and re-adding the same day is a correction, not two spells.

    An ended assignment stays on file — last year's compliance report has to
    keep reading true — and the row is unique on person, position and start
    date. So putting somebody back the same day collided with their own ended
    row and answered 500, which is what the second acceptance round hit:
    "removing staff and then adding them back returns server error msg".
    """
    from sqlalchemy import text
    _, client = hse_manager
    person, _ = worker

    position = (await client.post("/api/v1/positions", json={
        "code": "SEALER", "name": "Case sealer operator",
    })).json()

    added = await client.post(f"/api/v1/positions/{position['id']}/holders",
                              json={"user_id": str(person.id)})
    assert added.status_code == 201, added.text

    removed = await client.delete(
        f"/api/v1/positions/{position['id']}/holders/{added.json()['assignment_id']}")
    assert removed.status_code == 204

    again = await client.post(f"/api/v1/positions/{position['id']}/holders",
                              json={"user_id": str(person.id)})
    assert again.status_code == 201, again.text

    rows = (await db_session.execute(text(
        "SELECT count(*), count(*) FILTER (WHERE effective_to IS NULL)"
        "  FROM ehs_worker_positions WHERE user_id = :u AND position_id = :p"),
        {"u": person.id, "p": position["id"]})).one()
    assert rows == (1, 1), "one row, in force — not a second spell in the same job"


async def test_a_course_can_hold_more_than_one_file(hse_manager):
    """A course is rarely one file.

    The deck, the handout, the sign-off sheet, last year's revision. It held
    one, and uploading a second replaced the first with nothing on screen
    saying so.
    """
    _, client = hse_manager
    course = (await client.post("/api/v1/training/courses", json={
        "code": "WAH", "name": "Working at Heights"})).json()

    listed = await client.get(f"/api/v1/training/courses/{course['id']}/materials")
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


async def test_the_training_register_answers_more_than_one_question(hse_manager, worker):
    """"Who has done this course", "what has this person done" and "what
    happened in March" are three questions of the same list."""
    from datetime import date, timedelta

    _, client = hse_manager
    person, _ = worker
    course = (await client.post("/api/v1/training/courses", json={
        "code": "WHMIS2", "name": "WHMIS Refresher"})).json()
    old_day = date.today() - timedelta(days=400)

    for when in (old_day, date.today()):
        created = await client.post("/api/v1/training/records", json={
            "user_id": str(person.id), "course_id": course["id"],
            "completed_on": when.isoformat(), "delivery": "external",
        })
        assert created.status_code in (200, 201), created.text

    everything = (await client.get("/api/v1/training/records")).json()
    assert len(everything) >= 2

    # By name — the way it gets asked standing in front of somebody.
    found = (await client.get("/api/v1/training/records?search=WHMIS Refresher")).json()
    assert found and all("WHMIS" in r["course_label"] for r in found), found

    # And by when, which is what a date range is for.
    recent = (await client.get(
        f"/api/v1/training/records?completed_from={date.today().isoformat()}")).json()
    assert all(r["completed_on"] >= date.today().isoformat() for r in recent)
    assert len(recent) < len(everything), (
        "a range that excludes the older record must return fewer rows — "
        "otherwise the filter is not applied and this test proves nothing")


async def test_a_certificate_from_outside_can_be_attached(hse_manager, worker):
    """The column was there and no endpoint could fill it.

    A record of external training exists to hold the ticket, and the ticket
    could not be attached: creating a record accepted a file id, which meant
    the caller had to already know one, and nothing produced one.
    """
    from datetime import date

    _, client = hse_manager
    person, _ = worker
    course = (await client.post("/api/v1/training/courses", json={
        "code": "FORK", "name": "Forklift"})).json()
    record = (await client.post("/api/v1/training/records", json={
        "user_id": str(person.id), "course_id": course["id"],
        "completed_on": date.today().isoformat(), "delivery": "external",
    })).json()

    assert "certificate_file_id" in record, (
        "the screen decides whether to offer 'attach' or 'replace' from this")
    assert record["certificate_file_id"] is None

    missing = await client.get(f"/api/v1/training/records/{record['id']}/certificate")
    assert missing.status_code == 404
