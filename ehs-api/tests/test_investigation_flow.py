"""Investigation, causes, and the cause tree with corrective actions on it.

The tree is the module's most opinionated screen: a root cause with nothing
being done about it reads as visibly incomplete, because that gap is what an
audit looks for. These tests assert the API says so rather than leaving the
frontend to work it out.
"""
from datetime import datetime, timedelta, timezone
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
