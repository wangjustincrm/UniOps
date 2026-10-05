"""The reporting-to-classification loop, end to end over HTTP.

This is the path that carries the module's legal weight: someone reports an
injury, it gets classified, and the statutory clocks that follow start from the
right instants.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

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
    # ★ Last year's 31 December, not this one's. An incident cannot be dated in
    # the future any more (HSE, round 4), and for most of the year "this
    # December" is. The boundary under test is unchanged: 8:30pm on the 31st in
    # Ontario is 1:30am on 1 January in UTC either way.
    year = datetime.now(ET).year - 1

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


class TestAnIncidentCannotBeDatedForward:
    """★ HSE, round 4: "refuse future dates on all 3 incident types. warn the
    user that they selected a future date by mistake."

    Not tidiness. MOL's forty-eight hours and WSIB's three business days both
    run from the occurrence, so a date typed a week ahead puts both clocks in
    the future: the incident sits outside every overdue report while the real
    deadline goes past unnoticed. Refused where the record is made, because a
    warning that can be clicked past is how the clock gets set wrong.
    """

    @pytest.mark.parametrize("form_kind", ["medical", "equipment", "near_miss"])
    async def test_all_three_forms_refuse_it(self, hse_manager, form_kind):
        _, client = hse_manager
        ahead = datetime.now(timezone.utc) + timedelta(days=3)

        refused = await client.post("/api/v1/incidents", json={
            "form_kind": form_kind,
            "title": "Dated next week by mistake",
            "occurred_at": ahead.isoformat(),
        })
        assert refused.status_code == 422, refused.text
        assert "future" in refused.text.lower()

    @pytest.mark.parametrize("form_kind", ["medical", "equipment", "near_miss"])
    async def test_all_three_forms_still_accept_now(self, hse_manager, form_kind):
        """★ The admission. Refusing everything satisfies the test above."""
        _, client = hse_manager
        accepted = await client.post("/api/v1/incidents", json={
            "form_kind": form_kind,
            "title": "Reported as it happened",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        })
        assert accepted.status_code in (200, 201), accepted.text

    async def test_a_report_written_up_days_later_is_still_accepted(self, hse_manager):
        """The common case, and the one a future-date rule must not catch.

        Paper from a night shift gets keyed in days afterwards, and the WSIB
        clock is supposed to run from the occurrence — so backdating is the
        correct behaviour, not a loophole.
        """
        _, client = hse_manager
        accepted = await client.post("/api/v1/incidents", json={
            "form_kind": "medical",
            "title": "From Friday night's sheet",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),
        })
        assert accepted.status_code in (200, 201), accepted.text

    async def test_a_phone_a_few_minutes_fast_is_not_treated_as_a_mistake(self, hse_manager):
        """Clock skew between a device and this server is not somebody typing
        the wrong date, and refusing it would make the form unusable on exactly
        the phones it is meant for."""
        _, client = hse_manager
        accepted = await client.post("/api/v1/incidents", json={
            "form_kind": "near_miss",
            "title": "Reported from a phone whose clock runs fast",
            "occurred_at": (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat(),
        })
        assert accepted.status_code in (200, 201), accepted.text


async def test_the_where_panel_counts_where_it_happened_not_who_was_hurt(
        hse_manager, db_session):
    """★ Round 4: "where it happened is part of the incident creation record, but
    that location is not accurate in the dashboard where people are getting hurt
    graph."

    It was not the location. The panel was grouped by `department_id`, and an
    incident's department is the department of the *person hurt* — so somebody
    from Production injured in the warehouse counted towards Production, and the
    area the reporter chose appeared on the dashboard nowhere.

    Both cuts are kept, because "which department's people keep getting hurt" is
    also a real question. This asserts they are different numbers, which is the
    whole point: a test that used the same area and department would pass on the
    broken version.
    """
    from sqlalchemy import text as sql_text

    _, client = hse_manager
    year = datetime.now(ET).year

    warehouse = uuid.uuid4()
    path = "Warehouse test/105 – Warehouse"
    await db_session.execute(sql_text(
        "insert into locations (id, code, name, level, path, depth, is_active)"
        " values (:id, :code, '105 – Warehouse', 'department', :path, 1, true)"),
        {"id": warehouse, "code": f"WH-{str(warehouse)[:6]}", "path": path})
    production = uuid.uuid4()
    await db_session.execute(sql_text(
        "insert into departments (id, code, name, is_active)"
        " values (:id, :code, :name, true)"),
        {"id": production, "code": f"PRD-{str(production)[:6]}",
         "name": f"Production {str(production)[:6]}"})
    await db_session.commit()

    before_loc = {r["label"]: r["count"] for r in (await client.get(
        f"/api/v1/dashboard?year={year}")).json()["injured_locations"]}

    # Hurt in the warehouse; works for production.
    made = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Slipped in the warehouse",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "incident_type": "first_aid",
        "location_id": str(warehouse),
        "department_id": str(production),
    })
    assert made.status_code in (200, 201), made.text
    await client.post(f"/api/v1/incidents/{made.json()['id']}/submit")

    figures = (await client.get(f"/api/v1/dashboard?year={year}")).json()

    by_location = {r["label"]: r["count"] for r in figures["injured_locations"]}
    assert by_location.get(path, 0) == \
        before_loc.get(path, 0) + 1, (
        "the injury is not counted against the area it happened in")

    # ★ And it is counted against the department too, under that department's
    # name — the two panels answer different questions and must not be the same
    # number by accident.
    by_department = {r["label"]: r["count"] for r in figures["injured_departments"]}
    assert any(str(production)[:6] in label for label in by_department), (
        "the department cut lost the incident")
    assert "105 – Warehouse" not in " ".join(by_department), (
        "the department panel is reporting an area, which is the original fault")


class TestWhichTypesAskHowBadlySomebodyWasHurt:
    """★ Round 4, TC-03: "add 'how bad was the person hurt' to all 'what kind of
    incident was this' to allow for dashboard reporting, except report only/no
    injury and fatality selections."

    Before this, only Fatality and Unconscious asked — so Property Damage,
    Occupational Illness, Security and Drug/Alcohol carried no severity at all
    and were invisible to every injury figure on the dashboard.

    Fatality is the interesting exclusion. HSE took it out of the question, and
    a death is also, always, time lost: asked nothing and left empty it would
    drop out of LTIFR, the one figure where a fatality most needs to appear. So
    it is not asked because the answer is not in doubt, and the type carries it.
    """

    async def test_the_types_that_ask_and_the_type_that_does_not(self, hse_manager):
        """★ Rewritten in round 5, same scenario and the opposite expectation.

        Round 4 hid the question on the three types that imply their own
        severity, and on Report Only, to stop two answers disagreeing. Round 5
        asked for it back, opened on the matching answer — "first aid injury
        should default the HB picker to first aid" — because the dashboard
        counts this field and a figure driven by something the form never
        displayed is one nobody can check. The disagreement it was protecting
        against is handled by the default instead, and an override is a real
        case: a first-aid injury can turn into a lost-time one.

        Fatality is now the only exclusion, confirmed in round 5: "there is no
        value in asking how bad the person was hurt on a fatality, we can infer
        it."
        """
        _, client = hse_manager
        types = {t["code"]: t for t in (await client.get("/api/v1/incidents/types")).json()}

        assert types["fatality"]["asks_injury_class"] is False

        for code in ("report_only", "first_aid", "medical_aid", "lost_time",
                     "near_miss", "property_damage", "occupational_illness",
                     "environmental", "security", "drug_alcohol", "unconscious"):
            assert types[code]["asks_injury_class"] is True, code

    async def test_a_fatality_is_recorded_as_lost_time_without_being_asked(self, hse_manager):
        """★ The consequence of excluding it. Not asked, and not lost."""
        _, client = hse_manager
        made = await client.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Fatal incident at the dryer",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident_type": "fatality",
        })
        assert made.status_code in (200, 201), made.text
        assert made.json()["injury_class"] == "lost_time"
        assert made.json()["mol_reportable"] is True

    async def test_property_damage_can_carry_an_injury_now(self, hse_manager):
        """A machine can be wrecked and somebody hurt in the same event, and the
        type used to make the second half unrecordable."""
        _, client = hse_manager
        made = await client.post("/api/v1/incidents", json={
            "form_kind": "equipment", "title": "Forklift into the racking",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident_type": "property_damage",
            "injury_class": "medical_aid",
        })
        assert made.status_code in (200, 201), made.text
        assert made.json()["injury_class"] == "medical_aid"

    async def test_nobody_hurt_is_recorded_as_no_injury_class(self, hse_manager):
        """★ The other half, which keeps the injury figures honest.

        A near miss where nobody was hurt must not count towards anything, and
        the screen sends no class at all for it rather than a value meaning
        "none" — the dashboard filters on the class being absent.
        """
        _, client = hse_manager
        made = await client.post("/api/v1/incidents", json={
            "form_kind": "near_miss", "title": "Pallet fell, nobody underneath",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident_type": "near_miss",
        })
        assert made.status_code in (200, 201), made.text
        assert made.json()["injury_class"] is None


class TestTheAreaOwnerHearsAboutIt:
    """★ Round 4, TC-18: "when the location where the incident is recorded on a
    form, ensure the area responsible owner is included in the initial
    notification message."

    Nobody had to be answerable for the notice reaching the right supervisor —
    it went to the configured group, or to HSE, and the person responsible for
    the part of the plant where it happened found out when somebody forwarded
    it.
    """

    async def test_the_owner_of_the_area_is_told(self, hse_manager, test_engine, db_session):
        import uuid as _uuid

        from sqlalchemy import text as sql_text

        from app.models.area_owner import AreaOwner
        from app.models.communication import Communication
        from tests.conftest import make_user

        _, client = hse_manager
        supervisor = await make_user(test_engine, role="area_supervisor",
                                     full_name="Area Owner Person")

        # An owner named for the building; the incident recorded against a line
        # inside it. The prefix match is the point — an owner is answerable for
        # what is under them, not only for the exact node.
        building = _uuid.uuid4()
        line = _uuid.uuid4()
        for loc_id, name, path in (
            (building, "103 – Filling", "Production/103 – Filling"),
            (line, "Line 1", "Production/103 – Filling/Line 1"),
        ):
            await db_session.execute(sql_text(
                "insert into locations (id, code, name, level, path, depth, is_active)"
                " values (:id, :code, :name, 'department', :path, 1, true)"),
                {"id": loc_id, "code": str(loc_id)[:8], "name": name, "path": path})
        db_session.add(AreaOwner(
            id=_uuid.uuid4(), user_id=supervisor.id, user_name=supervisor.full_name,
            location_id=building, location_path="Production/103 – Filling"))
        await db_session.commit()

        made = await client.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Caught a hand on Line 1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident_type": "first_aid",
            "location_id": str(line),
        })
        assert made.status_code in (200, 201), made.text

        mail = (await db_session.execute(
            select(Communication)
            .where(Communication.doc_id == uuid.UUID(made.json()["id"]),
                   Communication.template_key == "incident_reported"))).scalars().first()
        assert mail is not None, "nothing was queued"
        assert supervisor.email in mail.to_addrs, (
            f"the area owner was not told: {mail.to_addrs}")
        # ★ And the message says why they got it. An email with no stated reason
        # reads as a circular, and the next one gets ignored.
        assert "Production/103 – Filling" in (mail.body_text or "")
        # ★ Third person: this is one email to everybody on the list, so "you
        # are down for this area" was telling HSE and the notify group that they
        # are responsible for somewhere they are not.
        assert "Area Owner Person is copied as the area owner" in (mail.body_text or "")
        assert "You are receiving this because you" not in (mail.body_text or "")

    async def test_an_incident_with_no_area_still_notifies_hse(self, hse_manager, db_session):
        """★ The admission. Adding a recipient must not become a requirement for
        one: plenty of reports name no area, and those must still go out."""
        from app.models.communication import Communication

        user, client = hse_manager
        made = await client.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Somewhere unrecorded",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident_type": "first_aid",
        })
        assert made.status_code in (200, 201), made.text

        mail = (await db_session.execute(
            select(Communication)
            .where(Communication.doc_id == uuid.UUID(made.json()["id"]),
                   Communication.template_key == "incident_reported"))).scalars().first()
        assert mail is not None, "an incident with no area told nobody"
        assert "area owner" not in (mail.body_text or ""), (
            "it claims an area owner when there is no area")


async def test_the_report_says_which_kind_of_incident_was_chosen(hse_manager):
    """★ Round 4, on the submitted report: "the kind of incident … not
    displayed on the submitted form".

    The screen showed the form kind — medical, equipment, near miss — which is
    the form it arrived on, not the choice the reporter made from HSE's twelve
    options. The choice was stored, read by the notifier when deciding who to
    tell, and shown back to nobody.
    """
    from datetime import datetime, timedelta, timezone

    _, client = hse_manager
    created = await client.post("/api/v1/incidents", json={
        "form_kind": "equipment", "title": "Forklift backed into a pillar",
        "incident_type": "property_damage",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    })
    assert created.status_code in (200, 201), created.text
    assert created.json()["incident_type_label"] == "Property / Asset Damage"

    back = (await client.get(f"/api/v1/incidents/{created.json()['id']}")).json()
    assert back["incident_type_label"] == "Property / Asset Damage"

    # And a report filed before the field existed says nothing rather than
    # guessing — 59 of the 60 incidents on the dev database carry a type, and
    # the one that does not must not be labelled as something.
    plain = await client.post("/api/v1/incidents", json={
        "form_kind": "equipment", "title": "No type chosen",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    })
    assert plain.status_code in (200, 201), plain.text
    assert plain.json()["incident_type_label"] is None


class TestTheInjuryPickerOpensOnTheRightAnswer:
    """★ Round 5, TC-03: "when selecting unconscious, automatically default to
    medical aid for how badly was the person hurt. first aid injury should
    default the HB picker to first aid. lost time kind of incident is a HB lost
    time" — and from the same round's answers, "if we record a Report Only, no
    injury can be inferred and the picker can be automatically updated."

    The three types that imply their own severity used to hide the question
    altogether. They ask it now, opened on the matching answer: the dashboard
    counts this field, and a figure driven by something the form never showed
    is one nobody can check.
    """

    async def test_every_type_that_asks_says_what_to_open_on(self, hse_manager):
        _, client = hse_manager
        types = (await client.get("/api/v1/incidents/types")).json()
        by_code = {t["code"]: t for t in types}

        expected = {
            "report_only": "none",
            "first_aid": "first_aid",
            "medical_aid": "medical_aid",
            "lost_time": "lost_time",
            "unconscious": "medical_aid",
        }
        for code, default in expected.items():
            assert by_code[code]["asks_injury_class"] is True, f"{code} must ask"
            assert by_code[code]["default_injury_class"] == default, code

    async def test_a_fatality_is_still_not_asked(self, hse_manager):
        """Confirmed in round 5: "there is no value in asking how bad the person
        was hurt on a fatality, we can infer it"."""
        _, client = hse_manager
        types = {t["code"]: t for t in
                 (await client.get("/api/v1/incidents/types")).json()}
        assert types["fatality"]["asks_injury_class"] is False
        assert types["fatality"]["default_injury_class"] is None

    async def test_the_types_that_ask_without_a_default_still_ask(self, hse_manager):
        """A near miss or a damaged machine usually hurt nobody, but either can
        turn out to have — so the question is asked with nothing pre-chosen."""
        _, client = hse_manager
        types = {t["code"]: t for t in
                 (await client.get("/api/v1/incidents/types")).json()}
        for code in ("near_miss", "property_damage", "environmental"):
            assert types[code]["asks_injury_class"] is True
            assert types[code]["default_injury_class"] is None

    async def test_what_the_reporter_picks_beats_the_type(self, hse_manager):
        """★ The default is a starting point, not a rule.

        A first-aid case that turns out to be lost time is a real thing, and
        the form must not argue with the person filling it in.
        """
        from datetime import datetime, timedelta, timezone

        _, client = hse_manager
        created = await client.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Cut that needed stitches after all",
            "incident_type": "first_aid",
            "injury_class": "lost_time",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        })
        assert created.status_code in (200, 201), created.text
        assert created.json()["injury_class"] == "lost_time"

    async def test_leaving_it_empty_still_falls_back_to_the_type(self, hse_manager):
        """And an untouched picker is not a regression: the type is the
        fallback, which is what it has always been."""
        from datetime import datetime, timedelta, timezone

        _, client = hse_manager
        created = await client.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Plain first aid case",
            "incident_type": "first_aid",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        })
        assert created.status_code in (200, 201), created.text
        assert created.json()["injury_class"] == "first_aid"


async def test_nobody_was_hurt_beats_the_type_default(hse_manager):
    """★ Round 5 review (uniops-41): the picker was added to the three types that
    imply their own severity, and "Nobody was hurt" sent null — which the server
    reads as "not answered" and fills from the type. So a First Aid Injury marked
    as nobody hurt stored `first_aid` anyway, silently, which is the opposite of
    what the change claimed to do.

    Chosen and unanswered are different facts. Only the second may be overridden.
    """
    from datetime import datetime, timedelta, timezone

    _, client = hse_manager
    when = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    said_nobody = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Logged as first aid, nobody hurt",
        "incident_type": "first_aid", "injury_class": "none",
        "occurred_at": when,
    })
    assert said_nobody.status_code in (200, 201), said_nobody.text
    assert said_nobody.json()["injury_class"] is None

    # Unanswered still falls back, which is what keeps the existing records and
    # the dashboard honest.
    unanswered = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Logged as first aid, nothing said",
        "incident_type": "first_aid", "occurred_at": when,
    })
    assert unanswered.status_code in (200, 201), unanswered.text
    assert unanswered.json()["injury_class"] == "first_aid"
