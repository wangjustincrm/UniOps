"""First aid (Reg 1101) and the WSIB Form 7 package.

First aid is recorded on the injury form and nowhere else. The separate register
was rewritten into incidents in round 5 — migration 0051 — and its screen and
endpoints removed; what is left of it here is the pair of facts Regulation 1101
actually asks for, which is that every treatment is recorded and that the person
who gave it is named.

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


# ── First aid is an incident ────────────────────────────────────────────────


class TestFirstAidIsRecordedOnTheInjuryForm:
    """★ Round 5, answer box 6: "rewrite them as incident reports and remove the
    register."

    First aid moved onto the injury form two rounds ago and the register became a
    merged view — the rows entered before the move, plus every treatment recorded
    on an incident since. HSE called it redundant in three consecutive rounds;
    what kept it was the handful of rows that lived nowhere else.

    Migration 0051 rewrote those as incidents, so the screen, its endpoints and
    its crud are gone. These tests hold the line that matters: a treatment is
    still recorded, it is an incident, and it still reaches the register's
    reports.
    """

    async def test_a_treatment_is_recorded_as_an_incident(self, hse_manager, worker):
        _, client = hse_manager
        injured, _ = worker

        created = await client.post("/api/v1/incidents", json={
            "form_kind": "medical",
            "title": "Shallow cut to the left index finger",
            "incident_type": "first_aid",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "description": "Caught a lid edge.",
            "persons": [{"role": "injured",
                         "person_name": injured.full_name or "Test Worker",
                         "user_id": str(injured.id),
                         "body_part_label": "Left index finger",
                         "treatment": "Cleaned and dressed."}],
        })
        assert created.status_code in (200, 201), created.text
        assert created.json()["injury_class"] == "first_aid"
        submitted = await client.post(
            f"/api/v1/incidents/{created.json()['id']}/submit")
        assert submitted.status_code == 200, submitted.text

    async def test_who_gave_the_treatment_is_recorded(self, hse_manager):
        """Regulation 1101 asks for the first aider by name, and a visiting nurse
        has no account — so a written-in name is accepted."""
        _, client = hse_manager
        created = await client.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Grazed knuckle",
            "incident_type": "first_aid",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "persons": [
                {"role": "injured", "person_name": "Dario Okafor",
                 "treatment": "Dressing applied."},
                {"role": "first_aider", "person_name": "A visiting nurse"},
            ],
        })
        assert created.status_code in (200, 201), created.text
        roles = {p["role"]: p["person_name"] for p in created.json()["persons"]}
        assert roles["first_aider"] == "A visiting nurse"

    async def test_the_register_screen_is_gone(self, hse_manager):
        """★ Removed rather than left unreachable. An endpoint with no screen is
        how this module has repeatedly ended up with code nobody could get to."""
        _, client = hse_manager
        for path in ("/api/v1/first-aid", "/api/v1/first-aid/sources"):
            assert (await client.get(path)).status_code == 404, path

    async def test_first_aid_still_has_a_report(self, hse_manager):
        """★ The report entity was repointed rather than deleted. One that
        quietly returns nothing is worse than one that is gone."""
        from app.services import reporting

        entity = reporting.BY_KEY["first_aid"]
        assert entity.table == "ehs_incidents"
        assert entity.where == "injury_class = 'first_aid'"

        _, client = hse_manager
        got = await client.get(
            "/api/v1/reports/group?entity=first_aid&dimension=month")
        assert got.status_code == 200, got.text

        # And it counts first aid rather than every incident, which is what the
        # narrowing is for.
        everything = await client.get(
            "/api/v1/reports/group?entity=incident&dimension=month")
        assert everything.status_code == 200, everything.text
        first_aid_total = sum(r["count"] for r in got.json())
        all_total = sum(r["count"] for r in everything.json())
        assert first_aid_total <= all_total


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
