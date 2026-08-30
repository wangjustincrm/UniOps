"""The reporting-to-classification loop, end to end over HTTP.

This is the path that carries the module's legal weight: someone reports an
injury, it gets classified, and the statutory clocks that follow start from the
right instants.
"""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/Toronto")

# Friday evening, so the WSIB business-day count has to cross a weekend.
OCCURRED = datetime(2026, 8, 28, 22, 10, tzinfo=ET)


def _payload(**kw) -> dict:
    body = {
        "form_kind": "medical",
        "title": "Laceration to left hand at the capping head",
        "occurred_at": OCCURRED.isoformat(),
        "description": "Guard was off while clearing a jam.",
        "persons": [{"role": "injured", "person_name": "Dario Okafor"}],
    }
    body.update(kw)
    return body


# ── Anyone can report ───────────────────────────────────────────────────────

async def test_a_worker_can_report_an_incident(worker):
    _, client = worker
    r = await client.post("/api/v1/incidents", json=_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["incident_no"].startswith("INC-2026-")
    assert body["status"] == "draft"
    assert body["injury_class"] is None
    assert body["mol_reportable"] is False
    # Nothing is owed to a regulator until somebody classifies it.
    assert body["deadlines"] == []
    assert [p["person_name"] for p in body["persons"]] == ["Dario Okafor"]


async def test_incident_numbers_are_sequential_within_the_year(worker):
    _, client = worker
    first = (await client.post("/api/v1/incidents", json=_payload())).json()["incident_no"]
    second = (await client.post("/api/v1/incidents", json=_payload())).json()["incident_no"]
    assert int(second.rsplit("-", 1)[1]) == int(first.rsplit("-", 1)[1]) + 1


async def test_an_anonymous_report_keeps_no_visible_reporter(worker):
    _, client = worker
    body = (await client.post("/api/v1/incidents", json=_payload(is_anonymous=True))).json()
    assert body["is_anonymous"] is True
    assert body["reported_by_name"] is None


async def test_occurred_at_must_carry_a_timezone(worker):
    """Without an offset there is no way to know which local day it fell on,
    and the business-day count would be a guess."""
    _, client = worker
    r = await client.post("/api/v1/incidents", json=_payload(occurred_at="2026-08-28T22:10:00"))
    assert r.status_code == 422
    assert "timezone" in r.text


# ── Submitting ──────────────────────────────────────────────────────────────

async def test_submit_moves_it_out_of_draft(worker):
    _, client = worker
    created = (await client.post("/api/v1/incidents", json=_payload())).json()
    r = await client.post(f"/api/v1/incidents/{created['id']}/submit")
    assert r.status_code == 200
    assert r.json()["status"] == "submitted"
    assert r.json()["submitted_at"] is not None


async def test_submitting_twice_is_refused(worker):
    _, client = worker
    created = (await client.post("/api/v1/incidents", json=_payload())).json()
    await client.post(f"/api/v1/incidents/{created['id']}/submit")
    again = await client.post(f"/api/v1/incidents/{created['id']}/submit")
    assert again.status_code == 409


# ── Classification and the clocks it starts ─────────────────────────────────

async def test_a_worker_cannot_classify(worker):
    """Classification starts legal clocks, so it sits with investigation."""
    _, client = worker
    created = (await client.post("/api/v1/incidents", json=_payload())).json()
    await client.post(f"/api/v1/incidents/{created['id']}/submit")
    r = await client.post(f"/api/v1/incidents/{created['id']}/classify",
                          json={"injury_class": "lost_time"})
    assert r.status_code == 403


async def test_classifying_a_draft_is_refused(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    created = (await wclient.post("/api/v1/incidents", json=_payload())).json()
    r = await mclient.post(f"/api/v1/incidents/{created['id']}/classify",
                           json={"injury_class": "lost_time"})
    assert r.status_code == 409


async def test_first_aid_starts_no_clock(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    created = (await wclient.post("/api/v1/incidents", json=_payload())).json()
    await wclient.post(f"/api/v1/incidents/{created['id']}/submit")
    body = (await mclient.post(f"/api/v1/incidents/{created['id']}/classify",
                               json={"injury_class": "first_aid"})).json()
    assert body["injury_class"] == "first_aid"
    assert body["deadlines"] == []


async def test_lost_time_starts_the_wsib_clock_across_the_weekend(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    created = (await wclient.post("/api/v1/incidents", json=_payload())).json()
    await wclient.post(f"/api/v1/incidents/{created['id']}/submit")
    aware = datetime(2026, 8, 28, 23, 0, tzinfo=ET)
    body = (await mclient.post(
        f"/api/v1/incidents/{created['id']}/classify",
        json={"injury_class": "lost_time", "employer_aware_at": aware.isoformat()},
    )).json()

    clocks = {d["kind"]: d for d in body["deadlines"]}
    assert set(clocks) == {"wsib_form7"}
    due = datetime.fromisoformat(clocks["wsib_form7"]["due_at"]).astimezone(ET)
    # Friday awareness: Monday, Tuesday, Wednesday are the three business days.
    assert due.date().isoformat() == "2026-09-02"
    assert clocks["wsib_form7"]["clock_type"] == "business"


async def test_a_critical_lost_time_injury_starts_both_clocks_from_different_instants(
    worker, hse_manager
):
    """The case the old single-severity model could not express."""
    _, wclient = worker
    _, mclient = hse_manager
    created = (await wclient.post("/api/v1/incidents", json=_payload())).json()
    await wclient.post(f"/api/v1/incidents/{created['id']}/submit")
    aware = datetime(2026, 8, 29, 8, 30, tzinfo=ET)  # the morning after
    body = (await mclient.post(
        f"/api/v1/incidents/{created['id']}/classify",
        json={
            "injury_class": "lost_time",
            "mol_reportable": True,
            "mol_reportable_reason": "critical_injury",
            "employer_aware_at": aware.isoformat(),
        },
    )).json()

    assert body["injury_class"] == "lost_time"
    assert body["mol_reportable"] is True
    clocks = {d["kind"]: d for d in body["deadlines"]}
    assert set(clocks) == {"mol_48h", "wsib_form7"}

    # MOL runs 48 wall-clock hours from the occurrence, per OHSA s.51(1).
    mol_starts = datetime.fromisoformat(clocks["mol_48h"]["starts_at"]).astimezone(ET)
    mol_due = datetime.fromisoformat(clocks["mol_48h"]["due_at"]).astimezone(ET)
    assert mol_starts == OCCURRED
    assert mol_due == datetime(2026, 8, 30, 22, 10, tzinfo=ET)
    assert clocks["mol_48h"]["regulation_ref"] == "OHSA s.51(1)"

    # WSIB runs three business days from employer awareness — a different
    # instant, and a different kind of clock.
    wsib_starts = datetime.fromisoformat(clocks["wsib_form7"]["starts_at"]).astimezone(ET)
    assert wsib_starts == aware
    assert wsib_starts != mol_starts


async def test_mol_reportable_requires_a_reason(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    created = (await wclient.post("/api/v1/incidents", json=_payload())).json()
    await wclient.post(f"/api/v1/incidents/{created['id']}/submit")
    r = await mclient.post(f"/api/v1/incidents/{created['id']}/classify",
                           json={"mol_reportable": True})
    assert r.status_code == 422


async def test_reclassifying_does_not_duplicate_clocks(worker, hse_manager):
    """First assessment at the scene is often revised once someone has seen a
    doctor. Escalating first aid to lost time must start the WSIB clock, and
    doing it twice must not start it twice."""
    _, wclient = worker
    _, mclient = hse_manager
    created = (await wclient.post("/api/v1/incidents", json=_payload())).json()
    await wclient.post(f"/api/v1/incidents/{created['id']}/submit")

    first = (await mclient.post(f"/api/v1/incidents/{created['id']}/classify",
                                json={"injury_class": "first_aid"})).json()
    assert first["deadlines"] == []

    second = (await mclient.post(f"/api/v1/incidents/{created['id']}/classify",
                                 json={"injury_class": "lost_time"})).json()
    assert [d["kind"] for d in second["deadlines"]] == ["wsib_form7"]

    third = (await mclient.post(f"/api/v1/incidents/{created['id']}/classify",
                                json={"injury_class": "lost_time"})).json()
    assert [d["kind"] for d in third["deadlines"]] == ["wsib_form7"]


async def test_classification_moves_it_to_under_investigation(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    created = (await wclient.post("/api/v1/incidents", json=_payload())).json()
    await wclient.post(f"/api/v1/incidents/{created['id']}/submit")
    body = (await mclient.post(f"/api/v1/incidents/{created['id']}/classify",
                               json={"injury_class": "medical_aid"})).json()
    assert body["status"] == "under_investigation"


# ── Reading ─────────────────────────────────────────────────────────────────

async def test_an_auditor_can_read_but_not_classify(worker, auditor):
    _, wclient = worker
    _, aclient = auditor
    created = (await wclient.post("/api/v1/incidents", json=_payload())).json()
    await wclient.post(f"/api/v1/incidents/{created['id']}/submit")

    assert (await aclient.get(f"/api/v1/incidents/{created['id']}")).status_code == 200
    assert (await aclient.post(f"/api/v1/incidents/{created['id']}/classify",
                               json={"injury_class": "lost_time"})).status_code == 403


async def test_listing_filters_by_status(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    draft = (await wclient.post("/api/v1/incidents", json=_payload(title="stays a draft"))).json()
    submitted = (await wclient.post("/api/v1/incidents", json=_payload(title="submitted one"))).json()
    await wclient.post(f"/api/v1/incidents/{submitted['id']}/submit")

    rows = (await mclient.get("/api/v1/incidents", params={"status": "submitted"})).json()
    ids = {r["id"] for r in rows}
    assert submitted["id"] in ids
    assert draft["id"] not in ids


async def test_unknown_incident_is_404(hse_manager):
    _, client = hse_manager
    r = await client.get(f"/api/v1/incidents/{uuid.uuid4()}")
    assert r.status_code == 404


# ── A reporter can see what they reported ───────────────────────────────────

async def test_a_worker_can_open_the_incident_they_just_reported(worker):
    """Submitting used to bounce the reporter off the page they were sent to:
    ehs.incident.read means "browse the register", which a worker does not
    have, and the guard did not make an exception for their own report."""
    _, client = worker
    created = (await client.post("/api/v1/incidents", json=_payload())).json()
    await client.post(f"/api/v1/incidents/{created['id']}/submit")

    assert (await client.get(f"/api/v1/incidents/{created['id']}")).status_code == 200
    assert (await client.get(f"/api/v1/incidents/{created['id']}/cause-tree")).status_code == 200


async def test_a_worker_cannot_open_someone_elses_incident(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    theirs = (await mclient.post("/api/v1/incidents", json=_payload(title="not yours"))).json()
    r = await wclient.get(f"/api/v1/incidents/{theirs['id']}")
    assert r.status_code == 403
    assert "reported or were involved in" in r.text


async def test_a_worker_can_open_an_incident_they_were_involved_in(worker, hse_manager):
    """Being the injured person is reason enough to see the record."""
    injured, wclient = worker
    _, mclient = hse_manager
    incident = (await mclient.post("/api/v1/incidents", json=_payload(
        persons=[{"role": "injured", "person_name": injured.full_name,
                  "user_id": str(injured.id)}]))).json()
    assert (await wclient.get(f"/api/v1/incidents/{incident['id']}")).status_code == 200


async def test_the_list_narrows_to_your_own_rather_than_refusing(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    mine = (await wclient.post("/api/v1/incidents", json=_payload(title="mine"))).json()
    theirs = (await mclient.post("/api/v1/incidents", json=_payload(title="theirs"))).json()

    rows = await wclient.get("/api/v1/incidents")
    assert rows.status_code == 200
    ids = {r["id"] for r in rows.json()}
    assert mine["id"] in ids
    assert theirs["id"] not in ids


async def test_someone_with_read_sees_the_whole_register(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    mine = (await wclient.post("/api/v1/incidents", json=_payload(title="mine"))).json()
    theirs = (await mclient.post("/api/v1/incidents", json=_payload(title="theirs"))).json()

    ids = {r["id"] for r in (await mclient.get("/api/v1/incidents")).json()}
    assert mine["id"] in ids
    assert theirs["id"] in ids
