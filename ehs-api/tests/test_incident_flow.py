"""The reporting-to-classification loop, end to end over HTTP.

This is the path that carries the module's legal weight: someone reports an
injury, it gets classified, and the statutory clocks that follow start from the
right instants.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
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


async def test_a_closed_incident_refuses_changes(hse_manager, worker):
    """A closed incident is a finished record, not a working document.

    It is what an inspector is shown, what the corrective actions were raised
    from, and what the statutory clocks were measured against. Editing it
    afterwards changes history with nothing to say it happened — which is what
    the first acceptance round found: "incidents are still updatable after the
    incidents are reviewed or closed."
    """
    _, client = hse_manager
    injured, _ = worker
    inc = (await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Slipped on the ramp",
        "incident_type": "first_aid",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "description": "Lost footing on the wet ramp.",
        "injured_user_id": str(injured.id),
    })).json()
    await client.post(f"/api/v1/incidents/{inc['id']}/submit")
    await client.post(f"/api/v1/incidents/{inc['id']}/hse-review")
    closed = await client.post(f"/api/v1/incidents/{inc['id']}/close")
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"

    for path, body in (
        (f"/api/v1/incidents/{inc['id']}/investigation", {"sequence_of_events": "rewritten"}),
        (f"/api/v1/incidents/{inc['id']}/causes", {"causes": []}),
    ):
        r = await client.put(path, json=body)
        assert r.status_code == 409, f"{path} accepted a change to a closed incident"
        assert "Reopen it" in r.text, "the refusal has to say where a real correction goes"


async def test_reopening_is_recorded_and_costs_the_review(hse_manager, worker):
    """Read-only for ever is the wrong answer to a genuine correction.

    People work around it, usually by raising a second incident for the same
    event, which is worse than an edit anybody can see. So reopening exists —
    restricted, recorded, and it takes the HSE review with it, because the
    reviewed version is not the one that will be closed the second time.
    """
    _, client = hse_manager
    injured, _ = worker
    inc = (await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Caught a finger",
        "incident_type": "first_aid",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        "description": "Finger caught in the guard.",
        "injured_user_id": str(injured.id),
    })).json()
    await client.post(f"/api/v1/incidents/{inc['id']}/submit")
    await client.post(f"/api/v1/incidents/{inc['id']}/hse-review")
    await client.post(f"/api/v1/incidents/{inc['id']}/close")

    body = (await client.post(f"/api/v1/incidents/{inc['id']}/reopen")).json()
    assert body["status"] != "closed"
    assert body["closed_at"] is None
    assert body["reopened_at"] is not None
    assert body["reopened_by_name"], "who reopened it is the point of recording it"
    assert body["hse_reviewed_at"] is None, "the review does not survive the reopening"

    edited = await client.put(f"/api/v1/incidents/{inc['id']}/investigation",
                              json={"sequence_of_events": "what actually happened"})
    assert edited.status_code == 200

    # And it cannot be closed again until somebody has reviewed what it now says.
    again = await client.post(f"/api/v1/incidents/{inc['id']}/close")
    assert again.status_code == 409


async def test_a_misspelt_key_does_not_blank_the_investigation(hse_manager, worker):
    """A PUT replaces, so anything left out is cleared — and unknown keys were
    silently dropped, which meant one typo blanked the whole investigation and
    answered 200. Unrecoverably: there is no history on this table.

    Found the hard way, on real data.
    """
    _, client = hse_manager
    injured, _ = worker
    inc = (await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Struck by a pallet",
        "incident_type": "first_aid",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
        "description": "Pallet swung out.",
        "injured_user_id": str(injured.id),
    })).json()
    await client.post(f"/api/v1/incidents/{inc['id']}/submit")
    await client.put(f"/api/v1/incidents/{inc['id']}/investigation",
                     json={"sequence_of_events": "the account that must survive"})

    typo = await client.put(f"/api/v1/incidents/{inc['id']}/investigation",
                            json={"what_happened": "wrong key"})
    assert typo.status_code == 422

    kept = (await client.get(f"/api/v1/incidents/{inc['id']}/investigation")).json()
    assert kept["sequence_of_events"] == "the account that must survive"


async def test_a_talk_reaches_a_supervisor_who_holds_the_role_the_designed_way(
    hse_manager, test_engine
):
    """Supervisory roles are held alongside a job, not instead of one.

    `area_supervisor` is marked not-assignable-as-primary, so the way to hold
    it is as an additional role — and the toolbox talk looked only at
    `users.role`, so those supervisors received nothing. Nothing looked wrong
    either: the completion rate counted the deliveries that existed, so a talk
    delivered to nobody read as fully delivered.
    """
    from sqlalchemy import text
    from tests.conftest import make_user

    _, client = hse_manager
    supervisor = await make_user(test_engine, role="requester", full_name="Ken Boucher")
    async with test_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'area_supervisor')"),
            {"u": supervisor.id})

    offered = (await client.get("/api/v1/toolbox-talks/supervisors")).json()
    assert any(s["id"] == str(supervisor.id) for s in offered), (
        "a supervisor holding the role the way it is designed to be held must be "
        "offered the talk")


async def test_a_talk_can_go_to_some_supervisors_rather_than_all(hse_manager, test_engine):
    """Everybody is the right default, not the only option.

    A topic concerning two crews, sent to forty people, teaches thirty-eight of
    them to skip the next one.
    """
    from sqlalchemy import text
    from tests.conftest import make_user

    _, client = hse_manager
    one = await make_user(test_engine, role="requester", full_name="Chosen Supervisor")
    two = await make_user(test_engine, role="requester", full_name="Other Supervisor")
    async with test_engine.begin() as conn:
        for person in (one, two):
            await conn.execute(
                text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'area_supervisor')"),
                {"u": person.id})

    talk = (await client.post("/api/v1/toolbox-talks", json={
        "title": "Ladder inspection before use",
        "period_month": date.today().replace(day=1).isoformat(),
    })).json()

    sent = await client.post(f"/api/v1/toolbox-talks/{talk['id']}/publish",
                             json={"supervisor_ids": [str(one.id)]})
    assert sent.status_code == 200, sent.text
    got = {d["supervisor_name"] for d in sent.json()}
    assert "Chosen Supervisor" in got
    assert "Other Supervisor" not in got, "it went to somebody who was not chosen"


async def test_body_part_and_nature_reach_the_injury_figures(hse_manager, worker, db_session):
    """What the reporting form never asked for.

    Both fields have been accepted by this endpoint since launch and both
    vocabularies were populated in Settings, but no screen offered them — so
    the body-part figure on the dashboard had one entry in twenty-four
    records, typed as free text with no vocabulary id behind it, and WSIB
    Form 7 was short two of its required fields.

    Grouping is by label because that is what reads on the chart; the id is
    what makes the grouping trustworthy, so both are asserted.
    """
    from datetime import datetime, timezone

    _, client = hse_manager
    injured, _ = worker

    # Seeded here rather than read from Settings: the test database is built by
    # create_all, so it has the tables the migrations create but none of the
    # content they insert. A test that read the vocabulary would be asserting
    # against an empty list and passing for the wrong reason.
    import uuid as _uuid

    from app.models.vocabulary import Vocabulary, VocabularyItem

    db_session.add(Vocabulary(code="body_part", name="Body Part"))
    await db_session.flush()
    hand_id = _uuid.uuid4()
    db_session.add(VocabularyItem(
        id=hand_id, vocabulary_code="body_part", code="hand",
        label="Hand", sort_order=10, path="Hand"))
    await db_session.commit()
    hand = {"id": str(hand_id), "label": "Hand"}

    created = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Caught in the case sealer",
        "incident_type": "first_aid",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Reached past the guard.",
        "persons": [{
            "role": "injured",
            "person_name": injured.full_name or "Test Worker",
            "user_id": str(injured.id),
            "body_part_id": hand["id"],
            "body_part_label": hand["label"],
        }],
    })
    assert created.status_code in (200, 201), created.text

    detail = (await client.get(f"/api/v1/incidents/{created.json()['id']}")).json()
    person = next(p for p in detail["persons"] if p["role"] == "injured")
    assert person["body_part_label"] == hand["label"]
    assert person["body_part_id"] == hand["id"], (
        "the label alone cannot be grouped: it is a snapshot, and two spellings "
        "of the same part would count as two")

    figures = (await client.get("/api/v1/dashboard")).json()
    assert any(row["label"] == hand["label"] for row in figures["body_parts"]), (
        "a body part recorded on the form has to reach the figure that is "
        "supposed to be showing it")


# ── The register's date range ───────────────────────────────────────────────

async def test_a_late_evening_incident_belongs_to_the_day_it_happened(hse_manager):
    """★ 10pm on 31 August is 02:00 on 1 September in UTC.

    Filtering on the stored instant against a bare date would file this one
    under September, and the August range an inspector asked for would come
    back one incident short — with nothing on screen to say so.
    """
    _, client = hse_manager
    late = datetime(2026, 8, 31, 22, 10, tzinfo=ET)
    incident = (await client.post("/api/v1/incidents", json=_payload(
        title="Late shift, last day of the month",
        occurred_at=late.isoformat()))).json()

    august = await client.get(
        "/api/v1/incidents?occurred_from=2026-08-01&occurred_to=2026-08-31")
    assert august.status_code == 200, august.text
    assert incident["id"] in {r["id"] for r in august.json()}

    september = await client.get(
        "/api/v1/incidents?occurred_from=2026-09-01&occurred_to=2026-09-30")
    assert incident["id"] not in {r["id"] for r in september.json()}


async def test_the_range_admits_as_well_as_excludes(hse_manager):
    """A filter that returns nothing would pass the test above on its own."""
    _, client = hse_manager
    inside = (await client.post("/api/v1/incidents", json=_payload(
        title="Inside the range",
        occurred_at=datetime(2026, 7, 15, 9, 0, tzinfo=ET).isoformat()))).json()
    outside = (await client.post("/api/v1/incidents", json=_payload(
        title="Well outside it",
        occurred_at=datetime(2026, 3, 2, 9, 0, tzinfo=ET).isoformat()))).json()

    rows = await client.get(
        "/api/v1/incidents?occurred_from=2026-07-01&occurred_to=2026-07-31")
    ids = {r["id"] for r in rows.json()}
    assert inside["id"] in ids
    assert outside["id"] not in ids

    # And a range wide enough to hold both returns both — the assertion above
    # is about the range, not about a row having gone missing.
    #
    # ★ Asked without a range this would be flaky, and flaky for exactly the
    # reason this feature exists: the register answers with the fifty most
    # recent, the suite shares one database, and most of its incidents are
    # dated "now" — so March 2026 falls off the end once enough tests have
    # run before this one.
    whole_year = await client.get(
        "/api/v1/incidents?occurred_from=2026-01-01&occurred_to=2026-12-31&limit=200")
    assert {inside["id"], outside["id"]} <= {r["id"] for r in whole_year.json()}


# ── What the report form asks for ───────────────────────────────────────────

async def test_the_types_say_which_ones_want_the_injury_detail(hse_manager):
    """Section B appears on the report form for the types where somebody was
    hurt, and that rule is served rather than decided in the browser.

    A copy of it on the other side would drift from this one, and the way it
    would drift is silent: a type added later would simply never offer the
    WSIB detail, and nobody finds a question that was not asked.
    """
    _, client = hse_manager
    rows = await client.get("/api/v1/incidents/types")
    assert rows.status_code == 200, rows.text
    wants = {t["code"] for t in rows.json() if t["injury_detail"]}

    # Hurt: the three severities, occupational illness, and the two the
    # Ministry must hear about.
    assert wants == {"first_aid", "medical_aid", "lost_time",
                     "occupational_illness", "fatality", "unconscious"}

    # And the ones where nobody was: asked for it, the form would be asking a
    # near miss about its allergies.
    assert not wants & {"report_only", "near_miss", "property_damage",
                        "environmental", "security", "drug_alcohol"}


# ── The dashboard, and opening a figure to see what it counted ──────────────

async def test_new_years_eve_belongs_to_the_year_it_happened_in(hse_manager, db_session):
    """★ 8pm on 31 December in Ontario is already January in UTC.

    The dashboard compared `occurred_at` against a bare date, which Postgres
    reads in the session's timezone — UTC — while the month grouping beside it
    used Ontario time. So the last evening of December counted towards the next
    year, in a figure headed with this one. The file said otherwise at the top,
    which is how it survived: the comment described the intention and nobody
    checked it against the comparison.
    """
    from sqlalchemy import text as sql_text

    _, client = hse_manager
    year = datetime.now(ET).year

    # Measured as a difference, not as a count: other tests file injuries too,
    # and an assertion that December is non-empty would pass under the old
    # boundary as soon as any of them landed there.
    before = (await client.get(
        f"/api/v1/dashboard?year={year}")).json()["medical_events"][11]["first_aid"]

    late = datetime(year, 12, 31, 20, 30, tzinfo=ET)
    incident = (await client.post("/api/v1/incidents", json=_payload(
        title="Last evening of the year", incident_type="first_aid",
        occurred_at=late.isoformat()))).json()
    await client.post(f"/api/v1/incidents/{incident['id']}/submit")

    figures = (await client.get(f"/api/v1/dashboard?year={year}")).json()
    assert figures["medical_events"][11]["first_aid"] == before + 1, (
        "an injury at 8:30pm on 31 December belongs to that December, not to "
        "the January it already is in UTC")

    # And the headline agrees with the month it is drawn from — the two used
    # different boundaries, so this is the assertion that keeps them together.
    counted = (await db_session.execute(sql_text(
        "select count(*) from ehs_incidents "
        " where injury_class = 'first_aid'"
        "   and timezone('America/Toronto', occurred_at) >= :s"
        "   and timezone('America/Toronto', occurred_at) < :e"),
        {"s": datetime(year, 1, 1), "e": datetime(year + 1, 1, 1)})).scalar()
    assert figures["headline"]["first_aid"] == counted


async def test_a_figure_can_be_opened_and_shows_what_it_counted(hse_manager):
    """The drill-down filters have to select the same records the dashboard
    counted, or the reader counts the rows, gets a different number, and stops
    believing both."""
    _, client = hse_manager
    when = datetime(2026, 6, 10, 9, 0, tzinfo=ET)
    hurt = (await client.post("/api/v1/incidents", json=_payload(
        title="Sprain on the line", incident_type="lost_time",
        occurred_at=when.isoformat()))).json()
    unhurt = (await client.post("/api/v1/incidents", json=_payload(
        title="Nobody hurt", incident_type="report_only",
        occurred_at=when.isoformat()))).json()

    rows = await client.get(
        "/api/v1/incidents?injured_only=true&injury_class=lost_time"
        "&occurred_from=2026-06-01&occurred_to=2026-06-30&limit=200")
    assert rows.status_code == 200, rows.text
    ids = {r["id"] for r in rows.json()}
    assert hurt["id"] in ids
    assert unhurt["id"] not in ids, (
        "'Report Only / No Injury' carries no injury class and must not appear "
        "behind an injury figure")

    # Recordable is its own question: medical aid, lost time, or reportable to
    # the Ministry. Lost time is in it; a first-aid injury is not.
    first_aid = (await client.post("/api/v1/incidents", json=_payload(
        title="Grazed knuckle", incident_type="first_aid",
        occurred_at=when.isoformat()))).json()
    recordable = await client.get(
        "/api/v1/incidents?recordable_only=true"
        "&occurred_from=2026-06-01&occurred_to=2026-06-30&limit=200")
    rec_ids = {r["id"] for r in recordable.json()}
    assert hurt["id"] in rec_ids
    assert first_aid["id"] not in rec_ids


async def test_two_people_hurt_in_one_incident_count_once(hse_manager, db_session):
    """★ The body-part chart counts incidents, not injured people.

    It used to count rows in the persons table, so an incident that hurt two
    people's hands read as two — while opening the bar showed one record.
    Whichever number the reader trusted, the other one made them stop trusting
    the page.
    """
    # Seeded directly: the test database comes from `create_all`, so it has the
    # vocabulary tables but none of the content the migrations put in them.
    from app.models.vocabulary import Vocabulary, VocabularyItem

    _, client = hse_manager
    if not (await db_session.get(Vocabulary, "body_part")):
        db_session.add(Vocabulary(code="body_part", name="Body Part"))
        await db_session.flush()
    knee_id = uuid.uuid4()
    db_session.add(VocabularyItem(
        id=knee_id, vocabulary_code="body_part", code="knee-two-people",
        label="Knee", sort_order=20, path="Knee"))
    await db_session.commit()
    hand = {"id": str(knee_id), "label": "Knee"}
    year = datetime.now(ET).year
    before = next(
        (r["count"] for r in (await client.get(f"/api/v1/dashboard?year={year}")).json()
         ["body_parts"] if r["label"] == hand["label"]), 0)

    created = await client.post("/api/v1/incidents", json=_payload(
        title="Two hands in one incident", incident_type="first_aid",
        occurred_at=datetime.now(ET).isoformat(),
        persons=[
            {"role": "injured", "person_name": "One Worker",
             "body_part_id": hand["id"], "body_part_label": hand["label"]},
            {"role": "injured", "person_name": "Another Worker",
             "body_part_id": hand["id"], "body_part_label": hand["label"]},
        ]))
    assert created.status_code in (200, 201), created.text
    await client.post(f"/api/v1/incidents/{created.json()['id']}/submit")

    after = next(
        r["count"] for r in (await client.get(f"/api/v1/dashboard?year={year}")).json()
        ["body_parts"] if r["label"] == hand["label"])
    assert after == before + 1, "two people, one incident, one on the chart"

    # And the bar opens onto exactly that one record.
    rows = await client.get(
        f"/api/v1/incidents?body_part_label={hand['label']}&limit=200")
    assert rows.status_code == 200, rows.text
    assert sum(1 for r in rows.json() if r["id"] == created.json()["id"]) == 1
