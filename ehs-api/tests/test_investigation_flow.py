"""Investigation, causes, and the cause tree with corrective actions on it.

The tree is the module's most opinionated screen: a root cause with nothing
being done about it reads as visibly incomplete, because that gap is what an
audit looks for. These tests assert the API says so rather than leaving the
frontend to work it out.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/Toronto")
OCCURRED = datetime(2026, 8, 28, 22, 10, tzinfo=ET)
DUE = (datetime.now(timezone.utc).date() + timedelta(days=14)).isoformat()


async def _reported_and_classified(wclient, mclient) -> dict:
    created = (await wclient.post("/api/v1/incidents", json={
        "form_kind": "medical",
        "title": "Laceration to left hand at the capping head",
        "occurred_at": OCCURRED.isoformat(),
    })).json()
    await wclient.post(f"/api/v1/incidents/{created['id']}/submit")
    await mclient.post(f"/api/v1/incidents/{created['id']}/classify",
                       json={"injury_class": "lost_time"})
    return created


CAUSES = {"causes": [
    {"cause_type": "immediate", "label": "Guard removed to clear a jam, not refitted"},
    {"cause_type": "root", "label": "Capping head jams roughly twice per shift"},
    {"cause_type": "root", "label": "Jam-clearing has no written method"},
]}


# ── Investigation ───────────────────────────────────────────────────────────

async def test_investigation_cannot_start_on_a_draft(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    created = (await wclient.post("/api/v1/incidents", json={
        "form_kind": "equipment", "title": "t", "occurred_at": OCCURRED.isoformat(),
    })).json()
    r = await mclient.put(f"/api/v1/incidents/{created['id']}/investigation", json={})
    assert r.status_code == 409


async def test_investigation_is_idempotent(worker, hse_manager):
    _, wclient = worker
    investigator, mclient = hse_manager
    incident = await _reported_and_classified(wclient, mclient)

    first = (await mclient.put(f"/api/v1/incidents/{incident['id']}/investigation", json={
        "investigator_id": str(investigator.id),
        "sequence_of_events": "Operator reached in while the head was running.",
    })).json()
    second = (await mclient.put(f"/api/v1/incidents/{incident['id']}/investigation", json={
        "investigator_id": str(investigator.id),
        "sequence_of_events": "Corrected: the head had already stopped.",
    })).json()

    assert first["id"] == second["id"]
    assert second["sequence_of_events"].startswith("Corrected")
    assert second["investigator_name"] == investigator.full_name


async def test_a_worker_cannot_write_the_investigation(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _reported_and_classified(wclient, mclient)
    r = await wclient.put(f"/api/v1/incidents/{incident['id']}/investigation", json={})
    assert r.status_code == 403


async def test_signing_requires_an_investigation_first(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _reported_and_classified(wclient, mclient)
    r = await mclient.post(f"/api/v1/incidents/{incident['id']}/investigation/sign",
                           json={"signature": "data:image/png;base64,iVBORw0KGgo="})
    assert r.status_code == 409


async def test_signing_records_who_and_when(worker, hse_manager):
    _, wclient = worker
    signer, mclient = hse_manager
    incident = await _reported_and_classified(wclient, mclient)
    await mclient.put(f"/api/v1/incidents/{incident['id']}/investigation", json={})
    body = (await mclient.post(f"/api/v1/incidents/{incident['id']}/investigation/sign",
                               json={"signature": "data:image/png;base64,iVBORw0KGgo="})).json()
    assert body["signed_by_name"] == signer.full_name
    assert body["completed_at"] is not None


# ── The cause tree ──────────────────────────────────────────────────────────

async def test_root_causes_without_actions_are_flagged(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _reported_and_classified(wclient, mclient)

    tree = (await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json=CAUSES)).json()
    assert [c["label"] for c in tree["immediate"]] == [CAUSES["causes"][0]["label"]]
    assert len(tree["root"]) == 2
    assert tree["unaddressed_root_causes"] == 2
    assert all(c["needs_action"] for c in tree["root"])
    # An immediate cause describes what happened; it is not something to fix.
    assert not any(c["needs_action"] for c in tree["immediate"])


async def test_attaching_an_action_clears_the_flag_for_that_cause(worker, hse_manager):
    _, wclient = worker
    owner, _ = worker
    _, mclient = hse_manager
    incident = await _reported_and_classified(wclient, mclient)
    tree = (await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json=CAUSES)).json()
    first_root = tree["root"][0]

    await mclient.post("/api/v1/actions", json={
        "source_type": "incident", "source_id": incident["id"], "cause_id": first_root["id"],
        "title": "Fit an interlocked guard", "hierarchy_of_control": "engineering",
        "owner_id": str(owner.id), "due_date": DUE,
    })

    tree = (await mclient.get(f"/api/v1/incidents/{incident['id']}/cause-tree")).json()
    addressed = next(c for c in tree["root"] if c["id"] == first_root["id"])
    other = next(c for c in tree["root"] if c["id"] != first_root["id"])
    assert addressed["needs_action"] is False
    assert [a["title"] for a in addressed["actions"]] == ["Fit an interlocked guard"]
    assert other["needs_action"] is True
    assert tree["unaddressed_root_causes"] == 1


async def test_the_tree_shows_action_progress(worker, hse_manager):
    _, wclient = worker
    owner, _ = worker
    _, mclient = hse_manager
    incident = await _reported_and_classified(wclient, mclient)
    tree = (await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json=CAUSES)).json()
    cause = tree["root"][0]

    action = (await mclient.post("/api/v1/actions", json={
        "source_type": "incident", "source_id": incident["id"], "cause_id": cause["id"],
        "title": "Write and train the method", "owner_id": str(owner.id), "due_date": DUE,
    })).json()
    await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                       json={"body": "done", "new_status": "pending_verification"})
    await mclient.post(f"/api/v1/actions/{action['id']}/verify", json={"is_effective": True})

    tree = (await mclient.get(f"/api/v1/incidents/{incident['id']}/cause-tree")).json()
    shown = next(c for c in tree["root"] if c["id"] == cause["id"])
    assert shown["actions"][0]["status"] == "closed"


async def test_replacing_causes_keeps_ones_that_have_actions(worker, hse_manager):
    """Dropping a cause that an action points at would orphan the action and
    lose the link between a root cause and what is being done about it."""
    _, wclient = worker
    owner, _ = worker
    _, mclient = hse_manager
    incident = await _reported_and_classified(wclient, mclient)
    tree = (await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json=CAUSES)).json()
    attached = tree["root"][0]

    await mclient.post("/api/v1/actions", json={
        "source_type": "incident", "source_id": incident["id"], "cause_id": attached["id"],
        "title": "Fit an interlocked guard", "owner_id": str(owner.id), "due_date": DUE,
    })

    # Rewrite the causes, leaving out the one that has an action.
    rewritten = (await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json={
        "causes": [{"cause_type": "root", "label": "Something else entirely"}],
    })).json()

    labels = {c["label"] for c in rewritten["root"]}
    assert "Something else entirely" in labels
    assert attached["label"] in labels, "a cause with an action attached must survive"
    kept = next(c for c in rewritten["root"] if c["label"] == attached["label"])
    assert kept["id"] == attached["id"]
    assert len(kept["actions"]) == 1


async def test_replacing_causes_drops_ones_with_nothing_attached(worker, hse_manager):
    _, wclient = worker
    _, mclient = hse_manager
    incident = await _reported_and_classified(wclient, mclient)
    await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json=CAUSES)

    rewritten = (await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json={
        "causes": [{"cause_type": "root", "label": "Only this one now"}],
    })).json()
    assert [c["label"] for c in rewritten["root"]] == ["Only this one now"]
    assert rewritten["immediate"] == []


async def test_an_auditor_can_read_the_tree_but_not_edit_it(worker, hse_manager, auditor):
    _, wclient = worker
    _, mclient = hse_manager
    _, aclient = auditor
    incident = await _reported_and_classified(wclient, mclient)
    await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json=CAUSES)

    assert (await aclient.get(f"/api/v1/incidents/{incident['id']}/cause-tree")).status_code == 200
    assert (await aclient.put(f"/api/v1/incidents/{incident['id']}/causes",
                              json=CAUSES)).status_code == 403


async def test_section_b_refuses_a_field_it_does_not_know(hse_manager, worker):
    """The medical section writes every field it is given and blanks the rest.

    That is the right shape for a PUT of a whole form, but it left no defence
    against a key that is not recognised: it was dropped in silence and the
    record saved around it. This is the exact mechanism that destroyed an
    investigation on this module, and this table holds allergies, medicines
    and past medical history — information nobody can reconstruct.
    """
    from datetime import datetime, timezone

    _, client = hse_manager
    injured, _ = worker
    created = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Burn from a steam line",
        "incident_type": "medical_aid",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Reached across an unlagged line.",
        "persons": [{"role": "injured", "person_name": injured.full_name or "Test Worker",
                     "user_id": str(injured.id)}],
    })
    incident_id = created.json()["id"]
    await client.post(f"/api/v1/incidents/{incident_id}/submit")

    good = await client.put(f"/api/v1/incidents/{incident_id}/medical", json={
        "allergies": "Penicillin", "medicines": "None",
    })
    assert good.status_code == 200, good.text          # the admitting half

    bad = await client.put(f"/api/v1/incidents/{incident_id}/medical", json={
        "allergy": "Penicillin",                        # singular — wrong
    })
    assert bad.status_code == 422, (
        "an unknown field must be refused, not dropped: accepting it would "
        "also have blanked the allergies that were there")

    # And the good record is untouched by the rejected call.
    after = (await client.get(f"/api/v1/incidents/{incident_id}/medical")).json()
    assert after["allergies"] == "Penicillin"


async def test_the_investigation_can_correct_the_body_part(hse_manager, worker, db_session):
    """The reporter guesses; whoever assesses the injury knows.

    Without a way to correct it the first guess is what the injury figures and
    WSIB Form 7 carry forever.
    """
    import uuid as _uuid
    from datetime import datetime, timezone

    from app.models.vocabulary import Vocabulary, VocabularyItem

    db_session.add(Vocabulary(code="body_part", name="Body Part"))
    await db_session.flush()
    wrist = _uuid.uuid4()
    db_session.add(VocabularyItem(id=wrist, vocabulary_code="body_part", code="wrist",
                                  label="Wrist", sort_order=10, path="Wrist"))
    await db_session.commit()

    _, client = hse_manager
    injured, _ = worker
    created = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Fall from a step",
        "incident_type": "first_aid",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Landed awkwardly.",
        "persons": [{"role": "injured", "person_name": injured.full_name or "Test Worker",
                     "user_id": str(injured.id),
                     "body_part_label": "hand"}],       # what the reporter said
    })
    incident_id = created.json()["id"]
    await client.post(f"/api/v1/incidents/{incident_id}/submit")
    person_id = created.json()["persons"][0]["id"]

    fixed = await client.patch(
        f"/api/v1/incidents/{incident_id}/persons/{person_id}",
        json={"body_part_id": str(wrist), "body_part_label": "Wrist"})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["body_part_id"] == str(wrist)

    # Scoped by incident: an id from somewhere else is not reachable through
    # this incident's permission check.
    other = await client.post("/api/v1/incidents", json={
        "form_kind": "near_miss", "title": "Pallet wobbled",
        "incident_type": "near_miss",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Nobody underneath.", "talked_to_operator": "yes",
    })
    stray = await client.patch(
        f"/api/v1/incidents/{other.json()['id']}/persons/{person_id}",
        json={"body_part_label": "Elbow"})
    assert stray.status_code == 404, "a person must not be editable through another incident"


async def test_vitals_are_a_series_not_a_single_reading(hse_manager, worker):
    """Taken again is the point of taking them.

    A pulse of 110 reads one way on its own and another beside the 90 from
    five minutes earlier, and the trend is what decides whether somebody goes
    to hospital. The form held exactly one set.
    """
    from datetime import datetime, timezone

    _, client = hse_manager
    injured, _ = worker
    incident_id = (await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Faint in the dryer bay",
        "incident_type": "medical_aid",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Went down beside the packer.",
        "persons": [{"role": "injured", "person_name": injured.full_name or "Test Worker",
                     "user_id": str(injured.id)}],
    })).json()["id"]
    await client.post(f"/api/v1/incidents/{incident_id}/submit")

    saved = await client.put(f"/api/v1/incidents/{incident_id}/medical", json={
        "vitals_series": [
            {"at": "10:06", "pulse": "110 weak", "respirations": "24",
             "blood_pressure": "100/60", "pain_level": 3},
            {"at": "10:11", "pulse": "90", "respirations": "18",
             "blood_pressure": "115/70", "pain_level": 2},
        ],
    })
    assert saved.status_code == 200, saved.text

    back = (await client.get(f"/api/v1/incidents/{incident_id}/medical")).json()
    assert len(back["vitals_series"]) == 2, back["vitals_series"]
    assert back["vitals_series"][0]["pulse"] == "110 weak", (
        "free text on purpose — a numeric field would make a first aider drop "
        "the word that mattered")
    assert back["vitals_series"][1]["pain_level"] == 2

    # A row somebody added and thought better of is not an observation that
    # everything was blank.
    trimmed = await client.put(f"/api/v1/incidents/{incident_id}/medical", json={
        "vitals_series": [
            {"at": "10:06", "pulse": "110 weak"},
            {"at": "", "pulse": "", "respirations": "", "blood_pressure": "",
             "pain_level": None, "pain_location": "", "other": ""},
        ],
    })
    assert len(trimmed.json()["vitals_series"]) == 1


@pytest.fixture
async def submitted_incident(worker, hse_manager) -> str:
    """An incident past draft, which is what recording causes requires."""
    _, wc = worker
    _, mc = hse_manager
    return (await _reported_and_classified(wc, mc))["id"]


@pytest.fixture
def an_owner(hse_manager) -> str:
    """Somebody to own the action. Required — an unowned action has no inbox to
    appear in, which is the failure this module exists to remove."""
    user, _ = hse_manager
    return str(user.id)


class TestACorrectiveActionRaisedWithItsCause:
    """★ Round 4, TC-04: "when adding a root cause on any form, the ability to
    raise a corrective action should be able to be completed on the same form at
    the same time. having to save the record first and then edit the corrective
    action is time wasting."

    One request, because an action points at a cause and a cause has no id until
    it is saved. Done in the client it is three steps whose middle one — reading
    back which id belongs to which of the rows just typed — is a guess.
    """

    async def test_the_cause_and_its_action_are_saved_together(
            self, hse_manager, submitted_incident, an_owner):
        _, client = hse_manager
        due = (date.today() + timedelta(days=14)).isoformat()

        saved = await client.put(
            f"/api/v1/incidents/{submitted_incident}/causes", json={"causes": [
                {"cause_type": "root", "label": "Guard was removed and not replaced",
                 "action": {"title": "Refit the guard and lock it",
                            "description": "Interlocked guard, not a warning label.",
                            "owner_id": an_owner, "due_date": due}},
            ]})
        assert saved.status_code == 200, saved.text

        tree = saved.json()
        assert len(tree["root"]) == 1
        cause = tree["root"][0]
        assert len(cause["actions"]) == 1, "the action was not raised with the cause"
        assert cause["actions"][0]["title"] == "Refit the guard and lock it"
        assert cause["actions"][0]["description"] == "Interlocked guard, not a warning label."
        # ★ And the cause no longer counts as unanswered, which is the figure an
        # audit reads.
        assert cause["needs_action"] is False
        assert tree["unaddressed_root_causes"] == 0

    async def test_saving_the_investigation_again_does_not_raise_a_second_action(
            self, hse_manager, submitted_incident, an_owner):
        """★ The one that matters. `replace_causes` runs on every save, and a
        screen that keeps the action it typed in its state would post it again —
        two identical corrective actions against one cause, each with its own
        inbox task and its own escalation ladder.
        """
        _, client = hse_manager
        due = (date.today() + timedelta(days=10)).isoformat()
        body = {"causes": [
            {"cause_type": "root", "label": "Procedure not followed",
             "action": {"title": "Re-brief the crew", "owner_id": an_owner, "due_date": due}},
        ]}

        first = await client.put(f"/api/v1/incidents/{submitted_incident}/causes", json=body)
        assert first.status_code == 200, first.text
        second = await client.put(f"/api/v1/incidents/{submitted_incident}/causes", json=body)
        assert second.status_code == 200, second.text

        actions = second.json()["root"][0]["actions"]
        assert len(actions) == 1, f"saving twice raised {len(actions)} actions"

    async def test_a_cause_with_no_action_is_still_flagged(
            self, hse_manager, submitted_incident):
        """The admission: the field is optional and the flag still works.

        A root cause with nothing under it is the gap an audit looks for, and
        making the action possible here must not make the flag stop appearing.
        """
        _, client = hse_manager
        saved = await client.put(
            f"/api/v1/incidents/{submitted_incident}/causes", json={"causes": [
                {"cause_type": "root", "label": "Nobody has decided what to do yet"},
            ]})
        assert saved.status_code == 200, saved.text
        assert saved.json()["root"][0]["needs_action"] is True
        assert saved.json()["unaddressed_root_causes"] == 1

    async def test_an_immediate_cause_is_not_offered_one(self, hse_manager, submitted_incident):
        """Accepted if sent, because refusing it would be a rule the screen does
        not need — but the screen only offers it on root causes, where the
        hierarchy of controls asks for the work to hang."""
        _, client = hse_manager
        saved = await client.put(
            f"/api/v1/incidents/{submitted_incident}/causes", json={"causes": [
                {"cause_type": "immediate", "label": "Unsafe design"},
            ]})
        assert saved.status_code == 200, saved.text
        assert saved.json()["immediate"][0]["needs_action"] is False
