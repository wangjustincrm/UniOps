"""★ Round 12, batch 4: job safety analyses.

JSA-01 System saved and shown by its current name; JSA-03 PPE from the list;
JSA-04 steps saved with the JSA in one request; JSA-05 / Q12 hazards and
controls picked per step, and approval counts them.
"""
import uuid

import pytest

from app.models.vocabulary import Vocabulary, VocabularyItem


@pytest.fixture
async def lists(db_session):
    for code in ("hazard", "hazard_control", "ppe", "equipment_system"):
        if await db_session.get(Vocabulary, code) is None:
            db_session.add(Vocabulary(code=code, name=code))
    await db_session.flush()
    tag = uuid.uuid4().hex[:6]
    items = {}
    for key, vocab, label in (("noise", "hazard", f"Noise {tag}"),
                              ("guard", "hazard_control", f"Fixed guard {tag}"),
                              ("plugs", "ppe", f"Ear plugs {tag}"),
                              ("filler", "equipment_system", f"Filler {tag}"),
                              ("gone", "equipment_system", f"Gone {tag}")):
        items[key] = VocabularyItem(id=uuid.uuid4(), vocabulary_code=vocab, code=f"{key}_{tag}",
                                    label=label, path=label, is_active=key != "gone")
        db_session.add(items[key])
    await db_session.commit()
    return items


async def test_steps_system_and_ppe_saved_with_the_jsa(hse_manager, lists, db_session):
    _, hse = hse_manager
    made = await hse.post("/api/v1/jsa", json={
        "title": "Change the filler nozzle",
        "system_id": str(lists["filler"].id),
        "ppe_ids": [str(lists["plugs"].id)],
        "steps": [
            {"step": "Lock out the filler", "hazard_ids": [str(lists["noise"].id)],
             "control_ids": [str(lists["guard"].id)]},
            {"step": "Swap the nozzle", "hazards": "Hot surface", "controls": "Let it cool"},
        ]})
    assert made.status_code == 201, made.text
    jsa = made.json()
    assert jsa["system_label"] == lists["filler"].label
    listed = await hse.get("/api/v1/jsa", params={"status": ["draft", "active"]})
    assert listed.status_code == 200, listed.text
    assert any(j["id"] == jsa["id"] and j["system_label"] == lists["filler"].label
               for j in listed.json()), "the list carries the System too"
    assert [p["label"] for p in jsa["ppe_items"]] == [lists["plugs"].label]
    steps = (await hse.get(f"/api/v1/jsa/{jsa['id']}/steps")).json()
    assert [s["step"] for s in steps] == ["Lock out the filler", "Swap the nozzle"], \
        "★ JSA-04: no second save to add the steps"
    assert steps[0]["hazard_items"][0]["label"] == lists["noise"].label
    assert steps[0]["control_items"][0]["label"] == lists["guard"].label

    lists["filler"].label = f"Filling {uuid.uuid4().hex[:4]}"
    await db_session.commit()
    assert (await hse.get(f"/api/v1/jsa/{jsa['id']}")).json()["system_label"] == lists["filler"].label, \
        "a System is shown by its current name"

    assert (await hse.post(f"/api/v1/jsa/{jsa['id']}/activate")).status_code == 200
    copy = (await hse.post(f"/api/v1/jsa/{jsa['id']}/new-version")).json()
    assert copy["system_id"] == str(lists["filler"].id)
    assert copy["ppe_items"] == jsa["ppe_items"]
    copied = (await hse.get(f"/api/v1/jsa/{copy['id']}/steps")).json()
    assert copied[0]["hazard_items"] == steps[0]["hazard_items"], "a new version keeps the picks"
    assert copied[0]["control_items"] == steps[0]["control_items"]


async def test_a_picked_hazard_with_no_control_is_not_approved(hse_manager, lists):
    _, hse = hse_manager
    jsa = (await hse.post("/api/v1/jsa", json={
        "title": "Clean the hopper",
        "steps": [{"step": "Climb in", "hazard_ids": [str(lists["noise"].id)]}]})).json()
    refused = await hse.post(f"/api/v1/jsa/{jsa['id']}/activate")
    assert refused.status_code == 422 and "no control" in refused.text, \
        "★ the gate read the words only, so picked hazards slipped through"

    fixed = await hse.put(f"/api/v1/jsa/{jsa['id']}/steps", json={"steps": [
        {"step": "Climb in", "hazard_ids": [str(lists["noise"].id)],
         "control_ids": [str(lists["guard"].id)]}]})
    assert fixed.status_code == 200, fixed.text
    assert (await hse.post(f"/api/v1/jsa/{jsa['id']}/activate")).status_code == 200


async def test_a_retired_system_and_a_wrong_list_are_refused(hse_manager, lists):
    _, hse = hse_manager
    assert (await hse.post("/api/v1/jsa", json={
        "title": "x", "system_id": str(lists["gone"].id)})).status_code == 422
    assert (await hse.post("/api/v1/jsa", json={
        "title": "x", "ppe_ids": [str(lists["noise"].id)]})).status_code == 422, \
        "a hazard is not PPE"
    jsa = (await hse.post("/api/v1/jsa", json={"title": "Fine"})).json()
    patched = await hse.patch(f"/api/v1/jsa/{jsa['id']}", json={"ppe_ids": [str(lists["plugs"].id)]})
    assert patched.status_code == 200 and patched.json()["ppe_items"][0]["label"] == lists["plugs"].label


async def test_a_draft_is_corrected_and_an_approved_one_is_not(hse_manager, lists):
    """★ B3/B4 review M5 and L8: the System and PPE could not be changed after
    the JSA was made; a null title was a 500; and an approved JSA must change
    by a new version."""
    _, hse = hse_manager
    jsa = (await hse.post("/api/v1/jsa", json={
        "title": "Fill the hopper", "steps": [{"step": "Open the lid"}]})).json()
    fixed = await hse.patch(f"/api/v1/jsa/{jsa['id']}", json={
        "system_id": str(lists["filler"].id), "ppe_ids": [str(lists["plugs"].id)]})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["system_label"] == lists["filler"].label
    assert (await hse.patch(f"/api/v1/jsa/{jsa['id']}", json={"title": None})).status_code == 422
    assert (await hse.post(f"/api/v1/jsa/{jsa['id']}/activate")).status_code == 200
    assert (await hse.patch(f"/api/v1/jsa/{jsa['id']}", json={"ppe_ids": []})).status_code == 409
