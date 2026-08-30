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


# ── The register ────────────────────────────────────────────────────────────

async def test_recording_a_treatment(hse_manager):
    _, client = hse_manager
    r = await client.post("/api/v1/first-aid", json=_entry())
    assert r.status_code == 201
    body = r.json()
    assert body["log_no"].startswith("FA-2026-")
    assert body["incident_id"] is None


async def test_a_first_aider_can_record_treatment(worker, test_engine, db_session):
    """The person who gave the treatment writes the entry — requiring an HSE
    Manager for that would mean the register is written from memory later."""
    from tests.conftest import authed_client, make_token, make_user
    aider = await make_user(test_engine, role="first_aider")
    async with authed_client(make_token(aider.id, aider.role), db_session) as client:
        r = await client.post("/api/v1/first-aid", json=_entry())
        assert r.status_code == 201


async def test_a_worker_cannot_write_the_register(worker):
    _, client = worker
    assert (await client.post("/api/v1/first-aid", json=_entry())).status_code == 403


async def test_occurred_at_must_carry_a_timezone(hse_manager):
    _, client = hse_manager
    r = await client.post("/api/v1/first-aid",
                          json=_entry(occurred_at="2026-08-24T14:30:00"))
    assert r.status_code == 422


async def test_the_register_can_be_filtered_by_whether_it_escalated(hse_manager):
    _, client = hse_manager
    plain = (await client.post("/api/v1/first-aid", json=_entry())).json()
    escalating = (await client.post("/api/v1/first-aid",
                                    json=_entry(injured_name="Sam Reid"))).json()
    await client.post(f"/api/v1/first-aid/{escalating['id']}/escalate",
                      json={"title": "Cut needed stitches"})

    unescalated = (await client.get("/api/v1/first-aid",
                                    params={"escalated": "false"})).json()
    ids = {e["id"] for e in unescalated}
    assert plain["id"] in ids
    assert escalating["id"] not in ids


# ── Escalation ──────────────────────────────────────────────────────────────

async def test_escalating_inherits_the_time_of_injury_not_today(hse_manager):
    """The injury happened when it happened. If this later turns out to be a
    critical injury, the MOL clock runs from that instant."""
    _, client = hse_manager
    entry = (await client.post("/api/v1/first-aid", json=_entry())).json()

    incident = (await client.post(f"/api/v1/first-aid/{entry['id']}/escalate",
                                  json={"title": "Cut required stitches"})).json()
    assert incident["occurred_at"] is not None
    assert datetime.fromisoformat(incident["occurred_at"]) == TREATED_AT
    assert incident["status"] == "submitted"
    assert incident["form_kind"] == "medical"


async def test_escalating_carries_the_injured_person_across(hse_manager):
    _, client = hse_manager
    entry = (await client.post("/api/v1/first-aid",
                               json=_entry(body_part_label="Left index finger"))).json()
    incident = (await client.post(f"/api/v1/first-aid/{entry['id']}/escalate",
                                  json={"title": "Cut required stitches"})).json()
    injured = [p for p in incident["persons"] if p["role"] == "injured"]
    assert len(injured) == 1
    assert injured[0]["person_name"] == "Dario Okafor"
    assert injured[0]["body_part_label"] == "Left index finger"


async def test_employer_awareness_defaults_to_now_not_to_the_injury(hse_manager):
    """The WSIB clock starts when the employer learns it is more than first
    aid, which is later than the treatment."""
    _, client = hse_manager
    entry = (await client.post("/api/v1/first-aid", json=_entry())).json()
    incident = (await client.post(f"/api/v1/first-aid/{entry['id']}/escalate",
                                  json={"title": "Needed stitches"})).json()
    aware = datetime.fromisoformat(incident["employer_aware_at"])
    assert aware > TREATED_AT


async def test_escalating_twice_is_refused(hse_manager):
    _, client = hse_manager
    entry = (await client.post("/api/v1/first-aid", json=_entry())).json()
    await client.post(f"/api/v1/first-aid/{entry['id']}/escalate", json={"title": "One"})
    again = await client.post(f"/api/v1/first-aid/{entry['id']}/escalate",
                              json={"title": "Two"})
    assert again.status_code == 409


async def test_the_register_entry_survives_escalation(hse_manager, db_session):
    """The entry stays the contemporaneous record of what was done at the
    time — escalation links it, it does not consume it."""
    _, client = hse_manager
    entry = (await client.post("/api/v1/first-aid", json=_entry())).json()
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
    injured = await make_user(test_engine, role="worker", full_name="Dario Okafor")
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
