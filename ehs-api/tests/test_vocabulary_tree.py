"""Lists that are trees (round 10).

Kirk: "Allow for a master hazard to be created, and a sub hazard(s) to be
added under it … create a new hazard class called Quality. under quality there
will be several sub items like, New quality issue, Sanitization process, and
Biological. Under Sanitization process, add Needs sanitization, Sanitization
out of date, Not followed." The rules are in crud/vocabulary.py's docstring.
"""
import uuid

import pytest

from app.models.vocabulary import Vocabulary
from tests.test_investigation_flow import _reported_and_classified

BASE = "/api/v1/settings/vocabularies"


@pytest.fixture
async def lists(db_session):
    db_session.add_all([
        Vocabulary(code="hazard", name="Hazard", is_hierarchical=True),
        Vocabulary(code="root_cause", name="Root Cause", is_hierarchical=True),
        Vocabulary(code="immediate_cause", name="Immediate Cause"),
        Vocabulary(code="ppe", name="PPE Type", is_hierarchical=True),
    ])
    await db_session.flush()


async def _add(client, label, parent=None, code="hazard", **extra):
    r = await client.post(f"{BASE}/{code}/items",
                          json={"label": label, "parent_id": parent, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def _kirk(client):
    quality = await _add(client, "Quality")
    sanit = await _add(client, "Sanitization process", quality["id"])
    leaf = await _add(client, "Not followed", sanit["id"])
    return quality, sanit, leaf


async def test_kirks_example_three_deep(hse_manager, lists):
    _, client = hse_manager
    quality, sanit, leaf = await _kirk(client)
    assert leaf["trail"] == "Quality › Sanitization process › Not followed"
    # No code typed: derived from the label.
    assert leaf["code"] == "NOT_FOLLOWED"
    rows = {r["id"]: r for r in (await client.get(f"{BASE}/hazard/items")).json()}
    assert rows[leaf["id"]]["trail"] == leaf["trail"]
    assert rows[sanit["id"]]["parent_id"] == quality["id"]


async def test_a_derived_code_that_is_taken_gets_a_number(hse_manager, lists):
    _, client = hse_manager
    a = await _add(client, "Biological")
    group = await _add(client, "Quality")
    b = await _add(client, "Biological", group["id"])
    assert (a["code"], b["code"]) == ("BIOLOGICAL", "BIOLOGICAL_2")


async def test_a_label_with_a_slash_is_one_level(hse_manager, lists):
    """path is '/'-joined; the trail comes from the parent chain."""
    _, client = hse_manager
    physical = await _add(client, "Physical")
    stf = await _add(client, "slips/trips/falls", physical["id"])
    assert stf["trail"] == "Physical › slips/trips/falls"


async def test_a_child_goes_under_an_entry_of_the_same_list(hse_manager, lists):
    _, client = hse_manager
    other = await _add(client, "People", code="root_cause")
    r = await client.post(f"{BASE}/hazard/items", json={"label": "X", "parent_id": other["id"]})
    assert r.status_code == 422
    assert "not on the Hazard list" in r.json()["detail"]


async def test_at_most_four_levels(hse_manager, lists):
    _, client = hse_manager
    parent = None
    for n in range(4):
        parent = (await _add(client, f"Level {n + 1}", parent and parent["id"]))
    r = await client.post(f"{BASE}/hazard/items", json={"label": "Five", "parent_id": parent["id"]})
    assert r.status_code == 422
    assert "4 levels" in r.json()["detail"]


async def test_a_trail_too_long_to_keep_on_a_record_is_refused(hse_manager, lists):
    _, client = hse_manager
    top = await _add(client, "A" * 150)
    r = await client.post(f"{BASE}/hazard/items", json={"label": "B" * 60, "parent_id": top["id"]})
    assert r.status_code == 422
    assert "characters" in r.json()["detail"]
    # Positive control: one that fits.
    await _add(client, "B" * 40, top["id"])


async def test_renaming_a_group_so_a_child_no_longer_fits_is_refused(hse_manager, lists):
    _, client = hse_manager
    top = await _add(client, "Top")
    await _add(client, "C" * 150, top["id"])
    r = await client.patch(f"{BASE}/hazard/items/{top['id']}", json={"label": "T" * 60})
    assert r.status_code == 422
    assert "C" * 20 in r.json()["detail"]


async def test_renaming_a_group_rewrites_everything_under_it(hse_manager, lists):
    _, client = hse_manager
    quality, sanit, leaf = await _kirk(client)
    await client.patch(f"{BASE}/hazard/items/{quality['id']}", json={"label": "Food safety"})
    rows = {r["id"]: r for r in (await client.get(f"{BASE}/hazard/items")).json()}
    assert rows[leaf["id"]]["trail"] == "Food safety › Sanitization process › Not followed"
    assert rows[leaf["id"]]["path"] == "Food safety/Sanitization process/Not followed"
    # The code does not follow the wording: code is what other code refers to.
    assert rows[quality["id"]]["code"] == "QUALITY"


async def test_retiring_a_group_retires_what_is_under_it(hse_manager, lists):
    _, client = hse_manager
    quality, sanit, leaf = await _kirk(client)
    await client.delete(f"{BASE}/hazard/items/{sanit['id']}")
    live = {r["id"] for r in (await client.get(f"{BASE}/hazard/items")).json()}
    assert quality["id"] in live
    assert sanit["id"] not in live and leaf["id"] not in live


async def test_restoring_a_group_restores_only_the_group(hse_manager, lists):
    """Something under it may have been retired on its own first."""
    _, client = hse_manager
    quality, sanit, leaf = await _kirk(client)
    await client.delete(f"{BASE}/hazard/items/{leaf['id']}")
    await client.delete(f"{BASE}/hazard/items/{sanit['id']}")
    r = await client.patch(f"{BASE}/hazard/items/{sanit['id']}", json={"is_active": True})
    assert r.status_code == 200
    live = {r["id"] for r in (await client.get(f"{BASE}/hazard/items")).json()}
    assert sanit["id"] in live and leaf["id"] not in live


async def test_an_entry_cannot_come_back_under_a_retired_group(hse_manager, lists):
    _, client = hse_manager
    quality, sanit, leaf = await _kirk(client)
    await client.delete(f"{BASE}/hazard/items/{quality['id']}")
    r = await client.patch(f"{BASE}/hazard/items/{leaf['id']}", json={"is_active": True})
    assert r.status_code == 422
    assert "Sanitization process" in r.json()["detail"]


async def test_nothing_goes_under_a_retired_entry(hse_manager, lists):
    _, client = hse_manager
    quality = await _add(client, "Quality")
    await client.delete(f"{BASE}/hazard/items/{quality['id']}")
    r = await client.post(f"{BASE}/hazard/items", json={"label": "X", "parent_id": quality["id"]})
    assert r.status_code == 422


class TestMoving:
    async def test_not_under_itself_or_inside_itself(self, hse_manager, lists):
        _, client = hse_manager
        quality, sanit, leaf = await _kirk(client)
        for target in (quality["id"], leaf["id"]):
            r = await client.patch(f"{BASE}/hazard/items/{quality['id']}", json={"parent_id": target})
            assert r.status_code == 422

    async def test_not_under_a_retired_entry(self, hse_manager, lists):
        _, client = hse_manager
        quality, sanit, leaf = await _kirk(client)
        old = await _add(client, "Old")
        await client.delete(f"{BASE}/hazard/items/{old['id']}")
        r = await client.patch(f"{BASE}/hazard/items/{sanit['id']}", json={"parent_id": old["id"]})
        assert r.status_code == 422

    async def test_depth_counts_what_moves_with_it(self, hse_manager, lists):
        _, client = hse_manager
        quality, sanit, leaf = await _kirk(client)  # three levels
        a = await _add(client, "A")
        b = await _add(client, "B", a["id"])
        # Quality (3 tall) under B (depth 2) → 5 deep.
        r = await client.patch(f"{BASE}/hazard/items/{quality['id']}", json={"parent_id": b["id"]})
        assert r.status_code == 422
        # Under A (depth 1) → 4 deep, allowed, and the paths follow.
        r = await client.patch(f"{BASE}/hazard/items/{quality['id']}", json={"parent_id": a["id"]})
        assert r.status_code == 200
        rows = {r["id"]: r for r in (await client.get(f"{BASE}/hazard/items")).json()}
        assert rows[leaf["id"]]["trail"] == "A › Quality › Sanitization process › Not followed"
        assert rows[leaf["id"]]["path"] == "A/Quality/Sanitization process/Not followed"


async def test_an_employee_gets_retired_entries_for_the_saved_values(worker, hse_manager, lists):
    """★ The pickers read include_inactive so a saved, since-retired entry is
    shown. That read must stay open to whoever can open the record."""
    _, wclient = worker
    _, mclient = hse_manager
    gone = await _add(mclient, "Retired one")
    await mclient.delete(f"{BASE}/hazard/items/{gone['id']}")
    r = await wclient.get(f"{BASE}/hazard/items?include_inactive=true")
    assert r.status_code == 200
    row = next(x for x in r.json() if x["id"] == gone["id"])
    assert row["is_active"] is False and row["trail"] == "Retired one"


class TestSnapshot:
    """★ What a record keeps is the server's trail, not the label sent."""

    async def test_a_cause_keeps_the_trail(self, worker, hse_manager, lists):
        _, wclient = worker
        _, mclient = hse_manager
        _, _, leaf = await _kirk(mclient)
        rc_top = await _add(mclient, "Procedures", code="root_cause")
        rc = await _add(mclient, "Not followed", rc_top["id"], code="root_cause")
        incident = await _reported_and_classified(wclient, mclient)
        tree = (await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json={"causes": [
            {"cause_type": "root", "vocabulary_item_id": rc["id"], "label": "whatever the page said"},
        ]})).json()
        assert [c["label"] for c in tree["root"]] == ["Procedures › Not followed"]
        listed = {r["id"]: r["trail"] for r in (await mclient.get(f"{BASE}/root_cause/items")).json()}
        assert tree["root"][0]["label"] == listed[rc["id"]]

    async def test_a_cause_from_another_list_is_refused(self, worker, hse_manager, lists):
        _, wclient = worker
        _, mclient = hse_manager
        _, _, hazard_leaf = await _kirk(mclient)
        incident = await _reported_and_classified(wclient, mclient)
        r = await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json={"causes": [
            {"cause_type": "root", "vocabulary_item_id": hazard_leaf["id"], "label": "x"},
        ]})
        assert r.status_code == 422

    async def test_a_cause_with_an_action_saved_under_its_old_wording_is_not_doubled(
            self, worker, hse_manager, lists, db_session):
        """Saved as "Not followed" before trails; resaved, it must match itself
        by entry rather than survive as a stray beside a new copy."""
        owner, wclient = worker
        _, mclient = hse_manager
        top = await _add(mclient, "Procedures", code="root_cause")
        rc = await _add(mclient, "Not followed", top["id"], code="root_cause")
        incident = await _reported_and_classified(wclient, mclient)
        tree = (await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json={"causes": [
            {"cause_type": "root", "vocabulary_item_id": rc["id"], "label": "x"}]})).json()
        cause_id = tree["root"][0]["id"]
        from sqlalchemy import text
        await db_session.execute(text("update ehs_causes set label = 'Not followed' where id = :i"),
                                 {"i": uuid.UUID(cause_id)})
        await db_session.flush()
        await mclient.post("/api/v1/actions", json={
            "source_type": "incident", "source_id": incident["id"], "cause_id": cause_id,
            "title": "Write the method down", "owner_id": str(owner.id), "due_date": "2030-01-01"})
        again = (await mclient.put(f"/api/v1/incidents/{incident['id']}/causes", json={"causes": [
            {"cause_type": "root", "vocabulary_item_id": rc["id"], "label": "Not followed"}]})).json()
        # Matched to itself, and kept in the wording it was recorded with.
        assert [(c["id"], c["label"]) for c in again["root"]] == [(cause_id, "Not followed")]

    async def test_a_hazard_keeps_the_trail(self, hse_manager, lists):
        _, client = hse_manager
        _, _, leaf = await _kirk(client)
        h = (await client.post("/api/v1/hazards", json={
            "title": "Line 2 filler not sanitised", "hazard_type_id": leaf["id"],
            "hazard_type_label": "Not followed"})).json()
        assert h["hazard_type_label"] == "Quality › Sanitization process › Not followed"
        other = await _add(client, "Biological", (await _add(client, "Bio group"))["id"])
        h2 = (await client.patch(f"/api/v1/hazards/{h['id']}", json={
            "hazard_type_id": other["id"], "hazard_type_label": "typed"})).json()
        assert h2["hazard_type_label"] == "Bio group › Biological"

    async def test_clearing_the_type_clears_its_wording(self, hse_manager, lists):
        _, client = hse_manager
        _, _, leaf = await _kirk(client)
        h = (await client.post("/api/v1/hazards", json={
            "title": "Filler", "hazard_type_id": leaf["id"]})).json()
        # A label sent without the id changes nothing.
        h = (await client.patch(f"/api/v1/hazards/{h['id']}", json={
            "hazard_type_label": "typed"})).json()
        assert h["hazard_type_label"] == "Quality › Sanitization process › Not followed"
        h = (await client.patch(f"/api/v1/hazards/{h['id']}", json={
            "hazard_type_id": None, "hazard_type_label": "still typed"})).json()
        assert (h["hazard_type_id"], h["hazard_type_label"]) == (None, None)

    async def test_a_label_without_an_id_is_not_kept_on_create(self, hse_manager, lists):
        _, client = hse_manager
        h = (await client.post("/api/v1/hazards", json={
            "title": "Filler", "hazard_type_label": "typed"})).json()
        assert h["hazard_type_label"] is None


async def test_usage_counts_records_by_id(worker, hse_manager, lists):
    """Asked before something is added under an entry: it becomes a heading."""
    _, wclient = worker
    _, mclient = hse_manager
    hz = await _add(mclient, "Noise")
    ppe = await _add(mclient, "Ear defenders", code="ppe")
    assert (await mclient.get(f"{BASE}/hazard/items/{hz['id']}/usage")).json()["total"] == 0
    await mclient.post("/api/v1/hazards", json={"title": "Loud", "hazard_type_id": hz["id"]})
    incident = await _reported_and_classified(wclient, mclient)
    await mclient.post(f"/api/v1/incidents/{incident['id']}/investigation")
    r = await mclient.put(f"/api/v1/incidents/{incident['id']}/investigation", json={
        "identified_hazard_ids": [hz["id"]], "ppe_that_could_prevent": [ppe["id"]]})
    assert r.status_code == 200, r.text
    used = (await mclient.get(f"{BASE}/hazard/items/{hz['id']}/usage")).json()
    assert used["total"] == 2
    assert {u["what"] for u in used["by"]} == {
        "Hazard register (type)", "Investigations (hazards identified)"}
    assert (await mclient.get(f"{BASE}/ppe/items/{ppe['id']}/usage")).json()["total"] == 1
    # Settings only.
    assert (await wclient.get(f"{BASE}/hazard/items/{hz['id']}/usage")).status_code == 403


class TestUnchangedKeepsItsWording:
    """★ Review (uniops-8b): pages send the id on every save. An entry that is
    still the one chosen keeps the wording it was recorded with; a newly
    chosen one takes the list's. Each pair: unchanged keeps, changed moves —
    "never recompute" would pass the first alone."""

    async def _relabel(self, db, table, column, row_id, label):
        from sqlalchemy import text
        await db.execute(text(f"update {table} set {column} = :l where id = :i"),
                         {"l": label, "i": uuid.UUID(row_id)})
        await db.flush()

    async def test_hazard(self, hse_manager, lists, db_session):
        _, client = hse_manager
        _, _, leaf = await _kirk(client)
        bio = await _add(client, "Biological", (await _add(client, "Bio group"))["id"])
        h = (await client.post("/api/v1/hazards", json={"title": "F", "hazard_type_id": leaf["id"]})).json()
        await self._relabel(db_session, "ehs_hazards", "hazard_type_label", h["id"], "Not followed")
        same = (await client.patch(f"/api/v1/hazards/{h['id']}", json={
            "description": "Only this", "hazard_type_id": leaf["id"], "hazard_type_label": "x"})).json()
        assert same["hazard_type_label"] == "Not followed"
        moved = (await client.patch(f"/api/v1/hazards/{h['id']}", json={
            "hazard_type_id": bio["id"], "hazard_type_label": "x"})).json()
        assert moved["hazard_type_label"] == "Bio group › Biological"

    async def test_cause_without_an_action(self, worker, hse_manager, lists, db_session):
        _, wclient = worker
        _, mclient = hse_manager
        top = await _add(mclient, "Procedures", code="root_cause")
        a = await _add(mclient, "Not followed", top["id"], code="root_cause")
        b = await _add(mclient, "None written", top["id"], code="root_cause")
        incident = await _reported_and_classified(wclient, mclient)
        url = f"/api/v1/incidents/{incident['id']}/causes"
        tree = (await mclient.put(url, json={"causes": [
            {"cause_type": "root", "vocabulary_item_id": a["id"], "label": "x"}]})).json()
        await self._relabel(db_session, "ehs_causes", "label", tree["root"][0]["id"], "Not followed")
        again = (await mclient.put(url, json={"causes": [
            {"cause_type": "root", "vocabulary_item_id": a["id"], "label": "x"},
            {"cause_type": "root", "vocabulary_item_id": b["id"], "label": "x"}]})).json()
        assert sorted(c["label"] for c in again["root"]) == ["Not followed", "Procedures › None written"]

    async def test_drill(self, hse_manager, lists, db_session):
        _, client = hse_manager
        top = await _add(client, "Procedures", code="root_cause")
        a = await _add(client, "Not followed", top["id"], code="root_cause")
        b = await _add(client, "None written", top["id"], code="root_cause")
        d = await client.post("/api/v1/drills", json={"drill_type": "fire", "scenario": "Probe"})
        assert d.status_code in (200, 201), d.text
        d = d.json()
        first = (await client.patch(f"/api/v1/drills/{d['id']}", json={"root_cause_id": a["id"]})).json()
        assert first["root_cause_label"] == "Procedures › Not followed"
        await self._relabel(db_session, "ehs_drills", "root_cause_label", d["id"], "Not followed")
        same = (await client.patch(f"/api/v1/drills/{d['id']}", json={
            "root_cause_id": a["id"], "observations": "Slow", "root_cause_label": "x"})).json()
        assert same["root_cause_label"] == "Not followed"
        moved = (await client.patch(f"/api/v1/drills/{d['id']}", json={"root_cause_id": b["id"]})).json()
        assert moved["root_cause_label"] == "Procedures › None written"
        cleared = (await client.patch(f"/api/v1/drills/{d['id']}", json={"root_cause_id": None})).json()
        assert cleared["root_cause_label"] is None

    async def test_form_answer(self, hse_manager, lists, db_session):
        _, client = hse_manager
        top = await _add(client, "Quality")
        a = await _add(client, "Not followed", top["id"])
        b = await _add(client, "Biological", top["id"])
        t = (await client.post("/api/v1/forms/templates", json={
            "code": f"vocab-{uuid.uuid4().hex[:5]}", "name": "Hazard check", "category": "inspection"})).json()
        r = await client.put(f"/api/v1/forms/templates/{t['id']}/schema", json={"schema": [
            {"key": "h", "type": "vocabulary", "vocabulary": "hazard", "label": "Hazard", "required": False}]})
        assert r.status_code in (200, 201), r.text
        r = await client.post(f"/api/v1/forms/templates/{t['id']}/publish", json={"change_note": "v1"})
        assert r.status_code in (200, 201), r.text
        sub = (await client.post(f"/api/v1/forms/templates/{t['id']}/submissions", json={})).json()
        url = f"/api/v1/forms/submissions/{sub['id']}/answers"
        first = (await client.put(url, json={"values": {"h": [{"id": a["id"], "label": "x"}]}})).json()
        row = next(x for x in first if x["question_key"] == "h")
        assert row["value_labels"] == ["Quality › Not followed"]
        from sqlalchemy import text
        await db_session.execute(text("update ehs_form_answers set value_labels = '[\"Not followed\"]'::jsonb "
                                      "where submission_id = :s and question_key = 'h'"), {"s": uuid.UUID(sub["id"])})
        await db_session.flush()
        again = (await client.put(url, json={"values": {"h": [
            {"id": a["id"], "label": "x"}, {"id": b["id"], "label": "y"}]}})).json()
        row = next(x for x in again if x["question_key"] == "h")
        assert row["value_labels"] == ["Not followed", "Quality › Biological"]
