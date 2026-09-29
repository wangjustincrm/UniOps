"""The first-aid register (Reg 1101) and the WSIB Form 7 package.

Two things worth stating up front, because both tests depend on them:

A first-aid entry that later turns out to need a doctor is the common route to
a WSIB obligation, and the register entry is usually the only contemporaneous
record of when the injury happened. Escalation therefore inherits the entry's
time, not today's.

UniOps does not file Form 7. What it produces is a data package plus the clock,
so these tests check that the package names what is missing rather than
pretending to be complete.
"""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy

from app.models.worker import WorkerProfile

ET = ZoneInfo("America/Toronto")
TREATED_AT = datetime(2026, 8, 24, 14, 30, tzinfo=ET)  # a Monday


def _entry(**kw) -> dict:
    body = {
        "occurred_at": TREATED_AT.isoformat(),
        "injured_name": "Dario Okafor",
        "treatment_given": "Cleaned and dressed a shallow cut to the left index finger.",
    }
    body.update(kw)
    return body


async def _legacy_entry(db_session, **kw):
    """A register row as the existing ones exist: written before first aid
    moved onto the injury form.

    Seeded directly rather than through the endpoint, because that endpoint is
    closed now. These rows are the Regulation 1101 record as it stands and were
    deliberately not migrated — so the behaviour they still need (listing,
    filtering, escalating) has to keep being covered.
    """
    import uuid as _uuid

    from app.models.first_aid import FirstAidLog

    seq = len((await db_session.execute(
        __import__("sqlalchemy").select(FirstAidLog))).scalars().all()) + 1
    row = FirstAidLog(
        id=_uuid.uuid4(),
        log_no=kw.pop("log_no", f"FA-2026-{seq:04d}"),
        occurred_at=kw.pop("occurred_at", TREATED_AT),
        injured_name=kw.pop("injured_name", "Dario Okafor"),
        treatment_given=kw.pop("treatment_given",
                               "Cleaned and dressed a shallow cut."),
        **kw,
    )
    db_session.add(row)
    await db_session.commit()
    return row


# ── The register ────────────────────────────────────────────────────────────

async def test_recording_a_treatment_goes_through_the_injury_form(hse_manager, worker):
    """First aid is an injury of class `first_aid`, recorded once.

    Two places recording the same event meant the same cut finger was entered
    twice, or once in whichever the person happened to open.
    """
    from datetime import datetime, timezone

    _, client = hse_manager
    injured, _ = worker
    created = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Shallow cut to the left index finger",
        "incident_type": "first_aid",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Caught a lid edge.",
        "persons": [{"role": "injured",
                     "person_name": injured.full_name or "Test Worker",
                     "user_id": str(injured.id),
                     "treatment": "Cleaned and dressed."}],
    })
    assert created.status_code in (200, 201), created.text
    await client.post(f"/api/v1/incidents/{created.json()['id']}/submit")

    register = (await client.get("/api/v1/first-aid")).json()
    assert any(e["incident_id"] == created.json()["id"] for e in register)


async def test_a_first_aider_can_record_the_treatment_they_gave(
        worker, test_engine, db_session):
    """The person who gave the treatment writes it down.

    Requiring an HSE Manager would mean the register is written from memory
    later. It is the incident form now, so the thing to check is that a first
    aider can file one.
    """
    from datetime import datetime, timezone

    from tests.conftest import authed_client, make_token, make_user
    aider = await make_user(test_engine, role="first_aider")
    async with authed_client(make_token(aider.id, aider.role), db_session) as client:
        r = await client.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Grazed knuckle",
            "incident_type": "first_aid",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "description": "Caught on a bolt head.",
        })
        assert r.status_code in (200, 201), r.text


async def test_a_worker_cannot_write_the_register(worker):
    _, client = worker
    assert (await client.post("/api/v1/first-aid", json=_entry())).status_code == 403


async def test_occurred_at_must_carry_a_timezone(hse_manager):
    _, client = hse_manager
    r = await client.post("/api/v1/first-aid",
                          json=_entry(occurred_at="2026-08-24T14:30:00"))
    assert r.status_code == 422


async def test_the_register_can_be_filtered_by_whether_it_escalated(
        hse_manager, db_session):
    _, client = hse_manager
    plain_row = await _legacy_entry(db_session)
    escalating_row = await _legacy_entry(db_session, injured_name="Sam Reid")
    plain = {"id": str(plain_row.id)}
    escalating = {"id": str(escalating_row.id)}
    await client.post(f"/api/v1/first-aid/{escalating['id']}/escalate",
                      json={"title": "Cut needed stitches"})

    unescalated = (await client.get("/api/v1/first-aid",
                                    params={"escalated": "false"})).json()
    ids = {e["id"] for e in unescalated}
    assert plain["id"] in ids
    assert escalating["id"] not in ids


# ── Escalation ──────────────────────────────────────────────────────────────

async def test_escalating_inherits_the_time_of_injury_not_today(hse_manager, db_session):
    """The injury happened when it happened. If this later turns out to be a
    critical injury, the MOL clock runs from that instant."""
    _, client = hse_manager
    row = await _legacy_entry(db_session)
    entry = {"id": str(row.id)}

    incident = (await client.post(f"/api/v1/first-aid/{entry['id']}/escalate",
                                  json={"title": "Cut required stitches"})).json()
    assert incident["occurred_at"] is not None
    assert datetime.fromisoformat(incident["occurred_at"]) == TREATED_AT
    assert incident["status"] == "submitted"
    assert incident["form_kind"] == "medical"


async def test_escalating_carries_the_injured_person_across(hse_manager, db_session):
    _, client = hse_manager
    row = await _legacy_entry(db_session, body_part_label="Left index finger")
    entry = {"id": str(row.id)}
    incident = (await client.post(f"/api/v1/first-aid/{entry['id']}/escalate",
                                  json={"title": "Cut required stitches"})).json()
    injured = [p for p in incident["persons"] if p["role"] == "injured"]
    assert len(injured) == 1
    assert injured[0]["person_name"] == "Dario Okafor"
    assert injured[0]["body_part_label"] == "Left index finger"


async def test_employer_awareness_defaults_to_now_not_to_the_injury(hse_manager, db_session):
    """The WSIB clock starts when the employer learns it is more than first
    aid, which is later than the treatment."""
    _, client = hse_manager
    row = await _legacy_entry(db_session)
    entry = {"id": str(row.id)}
    incident = (await client.post(f"/api/v1/first-aid/{entry['id']}/escalate",
                                  json={"title": "Needed stitches"})).json()
    aware = datetime.fromisoformat(incident["employer_aware_at"])
    assert aware > TREATED_AT


async def test_escalating_twice_is_refused(hse_manager, db_session):
    _, client = hse_manager
    row = await _legacy_entry(db_session)
    entry = {"id": str(row.id)}
    await client.post(f"/api/v1/first-aid/{entry['id']}/escalate", json={"title": "One"})
    again = await client.post(f"/api/v1/first-aid/{entry['id']}/escalate",
                              json={"title": "Two"})
    assert again.status_code == 409


async def test_the_register_entry_survives_escalation(hse_manager, db_session):
    """The entry stays the contemporaneous record of what was done at the
    time — escalation links it, it does not consume it."""
    _, client = hse_manager
    row = await _legacy_entry(db_session)
    entry = {"id": str(row.id), "treatment_given": row.treatment_given}
    await client.post(f"/api/v1/first-aid/{entry['id']}/escalate", json={"title": "x"})

    after = (await client.get(f"/api/v1/first-aid/{entry['id']}")).json()
    assert after["treatment_given"] == entry["treatment_given"]
    assert after["incident_id"] is not None


# ── WSIB Form 7 ─────────────────────────────────────────────────────────────

async def _lost_time_incident(wclient, mclient, *, with_person=True) -> dict:
    body = {
        "form_kind": "medical",
        "title": "Laceration to left hand",
        "occurred_at": datetime(2026, 8, 24, 9, 0, tzinfo=ET).isoformat(),
        "description": "Guard was off while clearing a jam.",
    }
    if with_person:
        body["persons"] = [{
            "role": "injured", "person_name": "Dario Okafor",
            "body_part_label": "Left hand", "nature_of_injury_label": "Laceration",
            "treatment": "Sutures at the walk-in clinic",
        }]
    incident = (await wclient.post("/api/v1/incidents", json=body)).json()
    await wclient.post(f"/api/v1/incidents/{incident['id']}/submit")
    await mclient.post(f"/api/v1/incidents/{incident['id']}/classify",
                       json={"injury_class": "lost_time"})
    return incident


async def test_the_package_gathers_what_we_hold(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _lost_time_incident(wclient, mclient)

    pkg = (await mclient.get(f"/api/v1/incidents/{incident['id']}/wsib-form7")).json()
    assert pkg["employer_name"] == "Canada Royal Milk"
    assert pkg["worker_name"] == "Dario Okafor"
    assert pkg["body_part"] == "Left hand"
    assert pkg["nature_of_injury"] == "Laceration"
    assert pkg["injury_class"] == "lost_time"
    assert pkg["due_at"] is not None


async def test_the_internal_target_is_earlier_than_the_legal_deadline(worker, hse_manager):
    """WSIB's limit is on receipt, so filing on the legal date is already too
    late if anything goes wrong in transit."""
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _lost_time_incident(wclient, mclient)
    pkg = (await mclient.get(f"/api/v1/incidents/{incident['id']}/wsib-form7")).json()
    assert datetime.fromisoformat(pkg["internal_target"]) < datetime.fromisoformat(pkg["due_at"])


async def test_the_package_names_what_is_missing(worker, hse_manager):
    """A silently incomplete package is worse than one that says what to go
    and find."""
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _lost_time_incident(wclient, mclient, with_person=False)

    pkg = (await mclient.get(f"/api/v1/incidents/{incident['id']}/wsib-form7")).json()
    assert pkg["is_complete"] is False
    missing = {m["field"] for m in pkg["missing"]}
    assert {"worker_name", "body_part", "nature_of_injury"} <= missing
    # Each one says why it matters rather than just naming a field.
    assert all(m["why_it_matters"] for m in pkg["missing"])


async def test_a_complete_package_says_so(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _lost_time_incident(wclient, mclient)
    pkg = (await mclient.get(f"/api/v1/incidents/{incident['id']}/wsib-form7")).json()
    assert pkg["missing"] == []
    assert pkg["is_complete"] is True


async def test_the_package_picks_up_the_worker_profile(
    worker, hse_manager, db_session, test_engine
):
    from tests.conftest import make_user
    _, wclient = worker
    _, mclient = hse_manager
    injured = await make_user(test_engine, role="requester", full_name="Dario Okafor")
    db_session.add(WorkerProfile(user_id=injured.id, employee_no="E-2041"))
    await db_session.flush()

    incident = (await wclient.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Laceration",
        "occurred_at": datetime(2026, 8, 24, 9, 0, tzinfo=ET).isoformat(),
        "description": "x",
        "persons": [{"role": "injured", "person_name": injured.full_name,
                     "user_id": str(injured.id), "body_part_label": "Left hand",
                     "nature_of_injury_label": "Laceration"}],
    })).json()
    await wclient.post(f"/api/v1/incidents/{incident['id']}/submit")
    await mclient.post(f"/api/v1/incidents/{incident['id']}/classify",
                       json={"injury_class": "lost_time"})

    pkg = (await mclient.get(f"/api/v1/incidents/{incident['id']}/wsib-form7")).json()
    assert pkg["worker_employee_no"] == "E-2041"
    assert pkg["worker_is_employee"] is True


# ── Recording that it was filed ─────────────────────────────────────────────

async def test_recording_the_filing_stops_the_clock(worker, hse_manager, db_session):
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _lost_time_incident(wclient, mclient)

    pkg = (await mclient.post(f"/api/v1/incidents/{incident['id']}/wsib-form7/filed",
                              json={"confirmation_number": "WSIB-2026-884213"})).json()
    assert pkg["filed_at"] is not None
    assert pkg["confirmation_number"] == "WSIB-2026-884213"

    satisfied = (await db_session.execute(sqlalchemy.text(
        "SELECT satisfied_at, evidence_note FROM ehs_statutory_deadlines"
        " WHERE source_id = :i AND kind = 'wsib_form7'"), {"i": incident["id"]})).mappings().one()
    assert satisfied["satisfied_at"] is not None
    assert satisfied["evidence_note"] == "WSIB-2026-884213"


async def test_a_confirmation_number_is_required(worker, hse_manager):
    """A deadline marked satisfied with nothing behind it is worse than one
    still showing as open."""
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _lost_time_incident(wclient, mclient)
    r = await mclient.post(f"/api/v1/incidents/{incident['id']}/wsib-form7/filed", json={})
    assert r.status_code == 422


async def test_filing_twice_is_refused(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _lost_time_incident(wclient, mclient)
    await mclient.post(f"/api/v1/incidents/{incident['id']}/wsib-form7/filed",
                       json={"confirmation_number": "A"})
    again = await mclient.post(f"/api/v1/incidents/{incident['id']}/wsib-form7/filed",
                               json={"confirmation_number": "B"})
    assert again.status_code == 409


async def test_filing_against_an_incident_that_owes_nothing_is_refused(worker, hse_manager):
    """A first-aid-only injury has no Form 7 obligation, and saying so beats
    recording a filing that never had to happen."""
    _, wclient = worker
    _, mclient = hse_manager
    incident = (await wclient.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Minor cut",
        "occurred_at": datetime(2026, 8, 24, 9, 0, tzinfo=ET).isoformat(),
    })).json()
    await wclient.post(f"/api/v1/incidents/{incident['id']}/submit")
    await mclient.post(f"/api/v1/incidents/{incident['id']}/classify",
                       json={"injury_class": "first_aid"})

    r = await mclient.post(f"/api/v1/incidents/{incident['id']}/wsib-form7/filed",
                           json={"confirmation_number": "X"})
    assert r.status_code == 409
    assert "owes WSIB nothing" in r.text


async def test_a_worker_cannot_record_a_filing(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _lost_time_incident(wclient, mclient)
    r = await wclient.post(f"/api/v1/incidents/{incident['id']}/wsib-form7/filed",
                           json={"confirmation_number": "X"})
    assert r.status_code == 403


async def test_the_ministry_clock_can_be_stopped(hse_manager, worker, test_engine):
    """Every statutory clock needs a way to be discharged by whoever met it.

    The Ministry's had none. WSIB's was stopped by recording the form and
    JHSC's by writing the reply, but s.51(1) kept counting after the report had
    been sent — so the compliance screen showed an overdue legal obligation
    that had in fact been met. An obligation nobody can discharge teaches
    people to ignore the one screen that must not be ignored.
    """
    _, client = hse_manager
    injured, _ = worker
    incident = (await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Caught in the case sealer",
        "incident_type": "unconscious",
        "injury_class": "lost_time",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "description": "Found unconscious beside the sealer.",
        "injured_user_id": str(injured.id),
    })).json()

    kinds = {d["kind"]: d for d in incident["deadlines"]}
    assert "mol_48h" in kinds, "an unconscious worker is reportable under s.51(1)"
    assert kinds["mol_48h"]["satisfied_at"] is None

    r = await client.post(f"/api/v1/incidents/{incident['id']}/mol-report/filed",
                          json={"reference": "Emailed to the Kingston office, ack 09:14"})
    assert r.status_code == 200, r.text
    after = {d["kind"]: d for d in r.json()["deadlines"]}
    assert after["mol_48h"]["satisfied_at"] is not None, "the clock must stop"

    # Recording it twice would overwrite the evidence of when it was actually met.
    again = await client.post(f"/api/v1/incidents/{incident['id']}/mol-report/filed",
                              json={"reference": "again"})
    assert again.status_code == 409


async def test_an_incident_owing_the_ministry_nothing_refuses_the_filing(hse_manager, worker):
    """Marking an obligation met that never existed is its own kind of false record."""
    _, client = hse_manager
    injured, _ = worker
    incident = (await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Cut finger",
        "incident_type": "first_aid",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "description": "Small cut, plaster applied.",
        "injured_user_id": str(injured.id),
    })).json()
    r = await client.post(f"/api/v1/incidents/{incident['id']}/mol-report/filed",
                          json={"reference": "should not be accepted"})
    assert r.status_code == 409


async def test_the_register_still_lists_everything_after_first_aid_moved(
        hse_manager, worker):
    """Regulation 1101 asks for a record of every treatment.

    First aid is recorded on the injury form now — two places recording the
    same event meant the same cut finger was entered twice, or once in
    whichever the person happened to open. But reading only the old table
    would have made the statutory register stop being complete at the moment
    of that change: a compliance regression caused by a usability fix.
    """
    from datetime import datetime, timezone

    _, client = hse_manager
    injured, _ = worker

    before = (await client.get("/api/v1/first-aid")).json()

    created = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Cut finger on a burr",
        "incident_type": "first_aid",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Caught the edge of a lid.",
        "persons": [{
            "role": "injured",
            "person_name": injured.full_name or "Test Worker",
            "user_id": str(injured.id),
            "treatment": "Cleaned and dressed.",
            "sent_offsite": False,
            "follow_up": "Check the dressing tomorrow.",
        }],
    })
    assert created.status_code in (200, 201), created.text
    incident_id = created.json()["id"]
    await client.post(f"/api/v1/incidents/{incident_id}/submit")

    after = (await client.get("/api/v1/first-aid")).json()
    assert len(after) == len(before) + 1, (
        "a first aid treatment recorded on the injury form has to appear in "
        "the register, or the register is no longer the register")

    entry = next(e for e in after if e.get("incident_id") == incident_id)
    assert entry["treatment_given"] == "Cleaned and dressed."
    assert entry["follow_up"] == "Check the dressing tomorrow."
    assert entry["injured_name"] == (injured.full_name or "Test Worker")


async def test_the_old_register_no_longer_takes_new_entries(hse_manager, worker):
    """Refused rather than removed, so a caller still pointed here is told
    where to go instead of getting a 404 and guessing."""
    from datetime import datetime, timezone

    _, client = hse_manager
    injured, _ = worker
    refused = await client.post("/api/v1/first-aid", json={
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "injured_name": injured.full_name or "Test Worker",
        "treatment_given": "Plaster.",
    })
    assert refused.status_code == 409, refused.text
    assert "injury form" in refused.json()["detail"]

    # And the entries already there are still readable — they were not
    # migrated, on purpose.
    listed = await client.get("/api/v1/first-aid")
    assert listed.status_code == 200


# ── The register's date range ───────────────────────────────────────────────

async def test_the_range_reaches_both_halves_of_the_register(hse_manager, db_session):
    """★ The range has to filter what is stored here *and* what comes from the
    injury form, or the statutory register answers a month with half of it.

    The list is served a hundred rows at a time, so this cannot be a filter
    applied to whatever arrived: a March asked for after a busy year would come
    back empty and read as a March with no treatments.
    """
    _, client = hse_manager
    legacy = await _legacy_entry(
        db_session, occurred_at=datetime(2026, 5, 12, 9, 15, tzinfo=ET),
        injured_name="Older register row")
    old = await _legacy_entry(
        db_session, occurred_at=datetime(2026, 1, 8, 9, 15, tzinfo=ET),
        injured_name="Well before the range")

    created = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Grazed knuckle",
        "incident_type": "first_aid",
        "occurred_at": datetime(2026, 5, 31, 21, 40, tzinfo=ET).isoformat(),
        "description": "Caught a lid edge.",
        "persons": [{"role": "injured", "person_name": "Dario Okafor",
                     "treatment": "Cleaned and dressed."}],
    })
    assert created.status_code in (200, 201), created.text
    await client.post(f"/api/v1/incidents/{created.json()['id']}/submit")

    may = await client.get("/api/v1/first-aid?occurred_from=2026-05-01&occurred_to=2026-05-31")
    assert may.status_code == 200, may.text
    rows = may.json()
    assert str(legacy.id) in {e["id"] for e in rows}
    # 9:40pm on the 31st: UTC calls it June, Ontario does not.
    assert created.json()["id"] in {e["incident_id"] for e in rows}
    assert str(old.id) not in {e["id"] for e in rows}

    # And a range wide enough to hold January has it again, so the assertion
    # above is about the range and not about a row having gone missing. Asked
    # without a range this would depend on how many entries the rest of the
    # suite had already written, which is the truncation this work is about.
    whole_year = await client.get(
        "/api/v1/first-aid?occurred_from=2026-01-01&occurred_to=2026-12-31")
    assert str(old.id) in {e["id"] for e in whole_year.json()}


class TestTheRegisterIsTheWholeRegister:
    """★ Round 4, TC-17: "the HSE role should be able to read all information
    regarding incidents and the first aid register."

    Two things were missing from it, and both are Regulation 1101 requirements
    rather than conveniences:

    - an injury form filed without naming who was hurt dropped out of the
      register entirely, because the query joined the injured-person row and
      insisted on it. Two of eighteen on the dev database;
    - who gave the treatment was never shown. The role existed on the model and
      nothing ever wrote it, so the column was empty for everything reported on
      an injury form.
    """

    async def test_a_first_aid_injury_with_nobody_named_is_still_in_the_register(
            self, hse_manager):
        _, client = hse_manager
        before = len((await client.get("/api/v1/first-aid")).json())

        made = await client.post("/api/v1/incidents", json={
            "form_kind": "medical",
            "title": "Dressed a cut, name not taken",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident_type": "first_aid",
        })
        assert made.status_code in (200, 201), made.text
        await client.post(f"/api/v1/incidents/{made.json()['id']}/submit")

        entries = (await client.get("/api/v1/first-aid")).json()
        assert len(entries) == before + 1, (
            "a treatment with no name on it is missing from the Regulation 1101 record")
        mine = next(e for e in entries if e["log_no"] == made.json()["incident_no"])
        assert mine["injured_name"] == "Not named", mine["injured_name"]

    async def test_the_register_says_who_gave_the_treatment(self, hse_manager, test_engine):
        """★ And the admission it needs: a named responder appears, so this is
        not passing because every entry says the same thing."""
        from tests.conftest import make_user

        _, client = hse_manager
        aider = await make_user(test_engine, role="requester", full_name="Priya Raman")

        made = await client.post("/api/v1/incidents", json={
            "form_kind": "medical",
            "title": "Shallow cut to a finger",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident_type": "first_aid",
            "persons": [
                {"role": "injured", "person_name": "Dario Okafor",
                 "treatment": "Cleaned and dressed"},
                {"role": "first_aider", "person_name": aider.full_name,
                 "user_id": str(aider.id)},
            ],
        })
        assert made.status_code in (200, 201), made.text
        await client.post(f"/api/v1/incidents/{made.json()['id']}/submit")

        entries = (await client.get("/api/v1/first-aid")).json()
        mine = next(e for e in entries if e["log_no"] == made.json()["incident_no"])
        assert mine["first_aider_name"] == "Priya Raman", mine
        assert mine["injured_name"] == "Dario Okafor"
        assert mine["treatment_given"] == "Cleaned and dressed"

    async def test_recording_a_treatment_in_the_old_register_is_still_refused(
            self, hse_manager):
        """HSE, round 4: "stop recording data on the first aid registry form."

        It already is, and this keeps it that way — the endpoint answers 409 and
        says where to go rather than 404, so a caller still pointed at it is told
        rather than left guessing.
        """
        _, client = hse_manager
        refused = await client.post("/api/v1/first-aid", json={
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "injured_name": "Somebody", "treatment_given": "A plaster",
        })
        assert refused.status_code == 409, refused.text
        assert "injury form" in refused.json()["detail"]
