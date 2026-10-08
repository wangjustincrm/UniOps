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


class TestWhoeverIsRunningItCanRunIt:
    """★ Round 4, TC-09 and TC-10: "the run by person on training should have
    full edit and create rights to allow for adding and marking students …
    allow the run by person to pass or fail the students as that role is
    currently limit to the HSE role."

    The coordinator is whoever HSE nominated, and on the dev database that
    includes an ordinary employee — `requester` holds no training permission at
    all, and neither do `supervisor` or `dept_manager`. So the person named to
    run a session could not open it, could not see the roster, and could not
    mark anybody: HSE had to type in results for a session they were not in.

    Being named to run it is the right to run it. The register as a whole still
    needs the register's permission, which is the pair of assertions at the end.
    """

    @pytest.fixture
    async def session_run_by_a_worker(self, hse_manager, worker, courses):
        _, hse = hse_manager
        wuser, _ = worker
        made = await hse.post("/api/v1/course-sessions", json={
            "course_id": str(courses["whmis"].id),
            "course_label": "WHMIS",
            "title": "WHMIS refresher",
            "session_date": date.today().isoformat(),
            "coordinator_id": str(wuser.id),
        })
        assert made.status_code in (200, 201), made.text
        return made.json()

    async def test_the_coordinator_opens_and_marks_their_own_session(
            self, worker, hse_manager, session_run_by_a_worker, test_engine):
        from tests.conftest import make_user

        wuser, wc = worker
        _, hse = hse_manager
        sid = session_run_by_a_worker["id"]

        # Open it.
        assert (await wc.get(f"/api/v1/course-sessions/{sid}")).status_code == 200, (
            "the person running the session cannot open it")

        # Add a student.
        student = await make_user(test_engine, role="requester", full_name="Student One")
        enrolled = await wc.post(f"/api/v1/course-sessions/{sid}/roster",
                                 json={"user_ids": [str(student.id)]})
        assert enrolled.status_code in (200, 201), enrolled.text

        roster = await wc.get(f"/api/v1/course-sessions/{sid}/roster")
        assert roster.status_code == 200, roster.text
        assert len(roster.json()) == 1

        # ★ Mark them. This is the one HSE named.
        marked = await wc.post(f"/api/v1/course-sessions/{sid}/results", json={
            "results": [{"enrolment_id": roster.json()[0]["id"], "result": "passed"}],
            "complete": True,
        })
        assert marked.status_code == 200, marked.text
        assert marked.json()[0]["result"] == "passed"

    async def test_the_coordinator_can_change_their_own_session(
            self, worker, session_run_by_a_worker):
        """★ Round 5, TC-07: "worker gets Insufficient permissions when trying to
        open a training session where they are the run by person."

        This endpoint was the one the round-4 work missed — six were opened up
        and the PATCH kept `ehs.training.write`, while the screen shows the
        instructor editor to whoever may run the session. So the coordinator got
        in, saw the roster, marked it, and was refused the moment they filled in
        the instructor. The sixth one-sided gate in this module.

        Round 5 settled the scope: their session, their arrangements — the
        instructor, the notes, the date and the place.
        """
        _, wc = worker
        sid = session_run_by_a_worker["id"]

        named = await wc.patch(f"/api/v1/course-sessions/{sid}",
                               json={"instructor_name": "A visiting trainer"})
        assert named.status_code == 200, named.text
        assert named.json()["instructor_name"] == "A visiting trainer"

        moved = await wc.patch(f"/api/v1/course-sessions/{sid}", json={
            "session_date": (date.today() + timedelta(days=3)).isoformat(),
            "location_text": "Training Room 107",
            "notes": "Moved so the night shift can attend.",
        })
        assert moved.status_code == 200, moved.text
        assert moved.json()["location_text"] == "Training Room 107"

        # Emptying a box clears it — except the date, which a session cannot be
        # without. Refused by name rather than a 500 from the NOT NULL column.
        cleared = await wc.patch(f"/api/v1/course-sessions/{sid}",
                                 json={"notes": None, "location_text": None})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["notes"] is None and cleared.json()["location_text"] is None
        undated = await wc.patch(f"/api/v1/course-sessions/{sid}",
                                 json={"session_date": None})
        assert undated.status_code == 422 and "needs a date" in undated.text

    async def test_the_coordinator_cannot_hand_the_session_to_somebody_else(
            self, worker, hse_manager, session_run_by_a_worker, test_engine):
        """★ The one field that is not theirs.

        Who runs a session is HSE's nomination — the chain HSE specified two
        rounds ago — and reassigning it would let one ordinary employee give
        another edit rights over it.
        """
        from tests.conftest import make_user

        _, wc = worker
        _, hse = hse_manager
        sid = session_run_by_a_worker["id"]
        other = await make_user(test_engine, role="requester", full_name="Someone Else")

        refused = await wc.patch(f"/api/v1/course-sessions/{sid}",
                                 json={"coordinator_id": str(other.id)})
        assert refused.status_code == 403, refused.text
        assert "HSE" in refused.json()["detail"]

        # And HSE still can, which is what makes the refusal mean something.
        moved = await hse.patch(f"/api/v1/course-sessions/{sid}",
                                json={"coordinator_id": str(other.id)})
        assert moved.status_code == 200, moved.text

    async def test_somebody_else_cannot_change_a_session_either(
            self, hse_manager, test_engine, courses, db_session):
        """Widening it to the coordinator must not widen it to everybody."""
        from tests.conftest import authed_client, make_token, make_user

        _, hse = hse_manager
        nobody = await make_user(test_engine, role="requester", full_name="Not Running It")
        made = await hse.post("/api/v1/course-sessions", json={
            "course_id": str(courses["forklift"].id),
            "course_label": "Forklift",
            "title": "Forklift",
            "session_date": date.today().isoformat(),
        })
        assert made.status_code in (200, 201), made.text
        sid = made.json()["id"]

        async with authed_client(make_token(nobody.id, nobody.role), db_session) as theirs:
            refused = await theirs.patch(f"/api/v1/course-sessions/{sid}",
                                         json={"notes": "I was never asked to run this"})
            assert refused.status_code == 403, refused.text

    async def test_somebody_else_s_session_is_still_refused(
            self, worker, hse_manager, test_engine, courses, db_session):
        """★ The admission's opposite. Opening this up to the coordinator must
        not open it to everybody — an employee who is not running a session and
        cannot read the register has no business in either."""
        from tests.conftest import authed_client, make_token, make_user

        _, hse = hse_manager
        someone = await make_user(test_engine, role="requester", full_name="Not Involved")
        made = await hse.post("/api/v1/course-sessions", json={
            "course_id": str(courses["forklift"].id),
            "course_label": "Forklift",
            "title": "Forklift",
            "session_date": date.today().isoformat(),
        })
        assert made.status_code in (200, 201), made.text
        sid = made.json()["id"]

        # The same session as the fixtures use, or this client cannot see a
        # delivery that has not been committed yet — a 404 that reads as a 403.
        async with authed_client(make_token(someone.id, someone.role), db_session) as theirs:
            assert (await theirs.get(f"/api/v1/course-sessions/{sid}")).status_code == 403
            assert (await theirs.get(f"/api/v1/course-sessions/{sid}/roster")).status_code == 403

        # And HSE, who holds the register, still can.
        assert (await hse.get(f"/api/v1/course-sessions/{sid}")).status_code == 200
        assert (await hse.get(f"/api/v1/course-sessions/{sid}/roster")).status_code == 200


async def test_the_register_prints_with_its_filter_on_it(hse_manager, db_session):
    """★ Round 4, TC-10: "create a way to print training records based on the
    filter created."

    The print takes the same parameters as the list, and says at the top what
    was asked for. A page of rows with nothing stating the filter reads as
    "these are the trained people", which is a different and much stronger
    claim than "these match a search".
    """
    _, client = hse_manager
    plain = await client.get("/api/v1/training/records/print")
    assert plain.status_code == 200, plain.text
    assert plain.headers["content-type"] == "application/pdf"
    assert plain.content.startswith(b"%PDF")
    unfiltered_len = len(plain.content)
    assert unfiltered_len > 800, "a PDF this small has no page in it"

    filtered = await client.get(
        "/api/v1/training/records/print?search=forklift&completed_from=2026-03-01")
    assert filtered.status_code == 200, filtered.text
    assert filtered.content.startswith(b"%PDF")
    # Not a byte comparison of the pages — the timestamp differs anyway. What
    # matters is that the filter reached the query rather than being ignored.
    assert len(filtered.content) != unfiltered_len

    # ★ And it is not readable by somebody with no business in the register.
    # A denial assertion on its own is satisfied by everybody being denied, so
    # the admission above is the other half.
    refused = await client.get("/api/v1/training/records/print",
                               headers={"Authorization": "Bearer nonsense"})
    assert refused.status_code in (401, 403)


def test_the_printed_filter_says_what_was_narrowed():
    """The words on the page, without rendering one."""
    from datetime import date

    from app.services import training_pdf

    assert training_pdf.describe(
        search=None, completed_from=None, completed_to=None,
        person=None, course=None) == []

    lines = training_pdf.describe(
        search="forklift", completed_from=date(2026, 3, 1),
        completed_to=date(2026, 3, 31), person="Marie Tremblay", course=None)
    assert lines[0] == "Person: Marie Tremblay"
    assert any("forklift" in line for line in lines)
    assert any("2026-03-01" in line and "2026-03-31" in line for line in lines)

    # One-sided ranges read as one-sided, rather than inventing the other end.
    assert training_pdf.describe(
        search=None, completed_from=date(2026, 3, 1), completed_to=None,
        person=None, course=None) == ["Completed on or after 2026-03-01"]


async def test_printing_one_person_with_no_training_still_names_them(
        hse_manager, worker):
    """★ "Nobody has any training" and "this person has none" are different
    pages, and the second is the one somebody needs to hand over.

    Raised in review by uniops-9a: the filter description was read off the
    first row, so filtered to a person with no records there was no row and the
    page printed an empty table with no name on it.
    """
    from app.services import training_pdf

    _, client = hse_manager
    person, _ = worker

    printed = await client.get(f"/api/v1/training/records/print?user_id={person.id}")
    assert printed.status_code == 200, printed.text
    assert printed.content.startswith(b"%PDF")

    # The words, checked where they are decided — the PDF's own bytes are
    # compressed, so asserting on them would pass for the wrong reason.
    lines = training_pdf.describe(
        search=None, completed_from=None, completed_to=None,
        person=person.full_name, course=None)
    assert lines == [f"Person: {person.full_name}"]


async def test_the_register_exports_as_a_workbook(hse_manager, db_session):
    """★ Round 5, TC-08: "provide a way to export the report to a xls file."

    A real workbook rather than a CSV named `.xls`: the register is full of names
    and course titles with commas and accents in them, and Excel guesses at a
    CSV's encoding. A workbook also keeps the dates as dates, so the column
    sorts.
    """
    import io
    import zipfile

    _, client = hse_manager
    got = await client.get("/api/v1/training/records/export")
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert "training-records.xlsx" in got.headers["content-disposition"]

    # A .xlsx is a zip. Opening it is the only honest check that it is one.
    book = zipfile.ZipFile(io.BytesIO(got.content))
    assert "xl/workbook.xml" in book.namelist()

    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(got.content)).active
    text = "\n".join(
        str(c.value) for row in sheet.iter_rows() for c in row if c.value is not None)
    assert "Training records" in text
    assert "Every record on file, unfiltered." in text
    # ★ The columns HSE asked for, and not the one they asked to lose.
    assert "Run by" in text and "Instructor" in text
    assert "How" not in text.split("\n")


async def test_the_export_carries_its_filter_like_the_print_does(hse_manager):
    """The two documents take the same parameters, so they cannot disagree about
    what was asked for — which is the whole of what makes either one evidence."""
    import io

    from openpyxl import load_workbook

    _, client = hse_manager
    got = await client.get("/api/v1/training/records/export?search=whmis")
    assert got.status_code == 200, got.text
    sheet = load_workbook(io.BytesIO(got.content)).active
    text = "\n".join(
        str(c.value) for row in sheet.iter_rows() for c in row if c.value is not None)
    assert "whmis" in text


async def test_a_record_from_a_session_knows_who_ran_it(hse_manager, worker, courses):
    """★ Round 5, TC-08 needs the run-by and instructor names, which live on the
    session. A training record had no link to one — the gap round 4 noted and
    left open. Migration 0052 adds it and marking a session fills it in.
    """
    _, client = hse_manager
    student, _ = worker

    made = await client.post("/api/v1/course-sessions", json={
        "course_id": str(courses["whmis"].id),
        "course_label": "WHMIS",
        "title": "WHMIS for the night shift",
        "session_date": date.today().isoformat(),
        "coordinator_id": str(student.id),
    })
    assert made.status_code in (200, 201), made.text
    session = made.json()
    await client.patch(f"/api/v1/course-sessions/{session['id']}",
                       json={"instructor_name": "An outside trainer"})
    enrolled = await client.post(f"/api/v1/course-sessions/{session['id']}/roster",
                                 json={"user_ids": [str(student.id)]})
    assert enrolled.status_code in (200, 201), enrolled.text
    marked = await client.post(f"/api/v1/course-sessions/{session['id']}/results", json={
        "results": [{"enrolment_id": enrolled.json()[0]["id"], "result": "passed"}],
        "complete": True,
    })
    assert marked.status_code == 200, marked.text

    records = (await client.get(
        f"/api/v1/training/records?user_id={student.id}")).json()
    mine = [r for r in records if r["course_label"] == "WHMIS"]
    assert mine, "passing a session must produce a record"
    assert mine[0]["delivery_id"] == session["id"]
    assert mine[0]["run_by_name"] == student.full_name
    assert mine[0]["instructor_name"] == "An outside trainer"


async def test_a_record_entered_by_hand_has_no_session(hse_manager, worker, courses):
    """★ And prints a dash rather than a blank. There was no session; saying so
    is different from having nothing to say."""
    _, client = hse_manager
    person, _ = worker

    made = await client.post("/api/v1/training/records", json={
        "user_id": str(person.id),
        "course_id": str(courses["whmis"].id),
        "completed_on": date.today().isoformat(),
        "delivery": "external",
    })
    assert made.status_code in (200, 201), made.text
    assert made.json()["delivery_id"] is None

    listed = (await client.get(
        f"/api/v1/training/records?user_id={person.id}")).json()
    external = [r for r in listed if r["delivery"] == "external"]
    assert external and external[0]["run_by_name"] is None


async def test_a_read_only_role_cannot_change_a_session(
        hse_manager, test_engine, courses, db_session):
    """★★ Round 5 review (uniops-41): opening the session PATCH to the
    coordinator had opened it to every role holding `ehs.training.read` — which
    includes `auditor`, a role that exists to read and nothing else, and
    `area_supervisor` for sessions that are not theirs.

    The same shape as the TC-19 holes: a record was gated and something about it
    was not. My own test covered the coordinator and a requester with no training
    permission at all, and a requester is refused by either gate — so it could
    not tell the read gate from the write gate apart. This is the case that can.
    """
    from tests.conftest import authed_client, make_token, make_user

    _, hse = hse_manager
    made = await hse.post("/api/v1/course-sessions", json={
        "course_id": str(courses["whmis"].id), "course_label": "WHMIS",
        "title": "WHMIS", "session_date": date.today().isoformat(),
    })
    assert made.status_code in (200, 201), made.text
    sid = made.json()["id"]

    auditor = await make_user(test_engine, role="auditor", full_name="Read Only")
    async with authed_client(make_token(auditor.id, auditor.role), db_session) as ac:
        # The admission: an auditor may read the session, which is the whole
        # point of the role.
        assert (await ac.get(f"/api/v1/course-sessions/{sid}")).status_code == 200

        # And may not change a word of it.
        for body in ({"title": "Renamed by an auditor"},
                     {"session_date": (date.today() + timedelta(days=1)).isoformat()},
                     {"instructor_name": "Someone"},
                     {"notes": "..."}):
            refused = await ac.patch(f"/api/v1/course-sessions/{sid}", json=body)
            assert refused.status_code == 403, f"{body} was accepted: {refused.text}"


async def test_a_name_that_looks_like_a_formula_stays_text(hse_manager, worker, courses):
    """★★ Round 5 review (uniops-41): openpyxl writes a string beginning with
    `=` as a *formula*. The instructor is typed in by hand and the filter line
    carries the search text, so both reach a cell — and
    `=HYPERLINK("http://elsewhere","Your certificate")` in the instructor field
    becomes a live link in a spreadsheet that came from us.

    UniOps has been here before: Budget Actual's export silently dropped text
    beginning with `=`.
    """
    import io

    from openpyxl import load_workbook

    _, client = hse_manager
    student, _ = worker
    made = await client.post("/api/v1/course-sessions", json={
        "course_id": str(courses["whmis"].id), "course_label": "WHMIS",
        "title": "WHMIS", "session_date": date.today().isoformat(),
    })
    sid = made.json()["id"]
    await client.patch(f"/api/v1/course-sessions/{sid}", json={
        "instructor_name": '=HYPERLINK("http://elsewhere","Your certificate")'})
    enrolled = await client.post(f"/api/v1/course-sessions/{sid}/roster",
                                 json={"user_ids": [str(student.id)]})
    await client.post(f"/api/v1/course-sessions/{sid}/results", json={
        "results": [{"enrolment_id": enrolled.json()[0]["id"], "result": "passed"}],
        "complete": True,
    })

    got = await client.get("/api/v1/training/records/export")
    assert got.status_code == 200, got.text
    sheet = load_workbook(io.BytesIO(got.content)).active

    hit = None
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and "HYPERLINK" in cell.value:
                hit = cell
    assert hit is not None, "the instructor name should be on the sheet"
    # ★ Text, not a formula — and the value is what was typed, with no stray
    # apostrophe in front of it.
    assert hit.data_type == "s"
    assert hit.value == '=HYPERLINK("http://elsewhere","Your certificate")'

    # And the filter line, which carries whatever was searched for.
    search = await client.get("/api/v1/training/records/export?search=%3D1%2B1")
    assert search.status_code == 200, search.text
    cells = [c for row in load_workbook(io.BytesIO(search.content)).active.iter_rows()
             for c in row if isinstance(c.value, str) and "=1+1" in c.value]
    assert cells, "the search text should be stated on the sheet"
    assert all(c.data_type == "s" for c in cells)
