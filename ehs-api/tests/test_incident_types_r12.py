"""★ Round 12, batch 3a: incident types.

INC-01 a food-safety near miss only coordinators, supervisors and HSE pick;
REP-01 it is in the Food Safety category, and changing away from it leaves the
category; Q4 a near miss never carries an injury class.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from app.models.incident import Incident


async def _food(db):
    await db.execute(text("""
        insert into ehs_vocabularies (code, name) values ('incident_category', 'Incident Category')
        on conflict (code) do nothing"""))
    await db.execute(text("""
        insert into ehs_vocabulary_items (id, vocabulary_code, code, label, sort_order, is_active, attrs, path)
        values (:id, 'incident_category', 'FOOD', 'Food Safety', 99, true, '{}'::jsonb, 'Food Safety')
        on conflict (vocabulary_code, code) do nothing"""), {"id": uuid.uuid4()})
    await db.commit()
    return (await db.execute(text(
        "select id from ehs_vocabulary_items where vocabulary_code='incident_category' and code='FOOD'"
    ))).scalar()


def _near_miss(code, **extra):
    return {"form_kind": "near_miss", "title": "Lid left off the blender",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident_type": code, **extra}


async def test_only_coordinators_supervisors_and_hse_see_or_pick_food_safety(worker, hse_manager):
    _, w = worker
    _, hse = hse_manager
    seen_by_worker = {t["code"] for t in (await w.get(
        "/api/v1/incidents/types", params={"form_kind": "near_miss"})).json()}
    seen_by_hse = {t["code"] for t in (await hse.get(
        "/api/v1/incidents/types", params={"form_kind": "near_miss"})).json()}
    assert seen_by_worker == {"near_miss"}
    assert seen_by_hse == {"near_miss", "near_miss_food_safety"}
    refused = await w.post("/api/v1/incidents", json=_near_miss("near_miss_food_safety"))
    assert refused.status_code == 403, refused.text
    assert (await w.post("/api/v1/incidents", json=_near_miss("near_miss"))).status_code == 201


async def test_a_food_safety_near_miss_is_in_the_category_until_its_type_changes(hse_manager, db_session):
    _, hse = hse_manager
    food = await _food(db_session)
    made = await hse.post("/api/v1/incidents", json=_near_miss("near_miss_food_safety"))
    assert made.status_code == 201, made.text
    iid = made.json()["id"]
    row = await db_session.get(Incident, uuid.UUID(iid))
    assert row.category_id == food and row.category_label == "Food Safety"
    assert (await hse.post(f"/api/v1/incidents/{iid}/submit")).status_code == 200

    moved = await hse.post(f"/api/v1/incidents/{iid}/reclassify",
                           json={"incident_type": "near_miss"})
    assert moved.status_code == 200, moved.text
    await db_session.refresh(row)
    assert row.category_id is None, "the category the old type set goes with it"


async def test_a_near_miss_never_records_an_injury(hse_manager, db_session):
    _, hse = hse_manager
    types = {t["code"]: t for t in (await hse.get("/api/v1/incidents/types",
                                                  params={"form_kind": "near_miss"})).json()}
    assert types["near_miss"]["asks_injury_class"] is False
    made = await hse.post("/api/v1/incidents", json=_near_miss("near_miss", injury_class="first_aid"))
    assert made.status_code == 201, made.text
    row = await db_session.get(Incident, uuid.UUID(made.json()["id"]))
    assert row.injury_class is None


async def test_an_investigation_is_outstanding_until_it_is_signed_off(hse_manager):
    """★ Round 12, Q6 and TC-05: the register's box and the list it opens."""
    _, hse = hse_manager

    async def summary():
        return (await hse.get("/api/v1/incidents/summary")).json()["investigations_outstanding"]

    async def listed(iid):
        rows = (await hse.get("/api/v1/incidents", params={
            "investigation_outstanding": "true", "limit": 200})).json()
        return iid in {r["id"] for r in rows}

    before = await summary()
    made = await hse.post("/api/v1/incidents", json={
        "form_kind": "equipment", "title": "Guard missing on the capper",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "incident_type": "property_damage", "batch_code": " LOT-2611-A "})
    assert made.status_code == 201, made.text
    iid = made.json()["id"]
    assert made.json()["batch_code"] == "LOT-2611-A"
    assert await summary() == before, "a draft is not counted"
    await hse.post(f"/api/v1/incidents/{iid}/submit")
    assert await summary() == before + 1, "an equipment incident owes an investigation"
    assert await listed(iid)

    saved = await hse.put(f"/api/v1/incidents/{iid}/investigation", json={
        "investigator_id": None, "supporting_investigator_ids": [], "identified_hazard_ids": [],
        "ppe_that_could_prevent": [], "sequence_of_events": "Guard left off after cleaning",
        "root_cause_narrative": None})
    assert saved.status_code == 200, saved.text
    assert await summary() == before + 1, "saved but not signed is still outstanding"
    signed = await hse.post(f"/api/v1/incidents/{iid}/investigation/sign",
                            json={"signature": "data:image/png;base64,iVBORw0KGgo="})
    assert signed.status_code == 200, signed.text
    assert await summary() == before
    assert not await listed(iid)

    edited = await hse.patch(f"/api/v1/incidents/{iid}", json={"batch_code": "LOT-2611-B"})
    assert edited.status_code == 200 and edited.json()["batch_code"] == "LOT-2611-B"


async def test_a_food_safety_near_miss_is_signed_off_without_all_five_whys(hse_manager, db_session):
    """★ Round 12, Q8 made the whys required on food safety; UAT 17: "remove the
    mandatory 5 why questions from a food safety near miss. make it optional."
    What is answered is still kept."""
    _, hse = hse_manager
    await _food(db_session)
    sig = {"signature": "data:image/png;base64,iVBORw0KGgo="}

    async def investigate(code, whys):
        made = await hse.post("/api/v1/incidents", json=_near_miss(code))
        iid = made.json()["id"]
        await hse.post(f"/api/v1/incidents/{iid}/submit")
        put = await hse.put(f"/api/v1/incidents/{iid}/investigation", json={
            "investigator_id": None, "five_whys": whys})
        assert put.status_code == 200, put.text
        return iid, (await hse.get(f"/api/v1/incidents/{iid}")).json()

    iid, inc = await investigate("near_miss_food_safety", ["Lid off", "Rushed", "", "", ""])
    assert inc["needs_five_whys"] is False
    # Still a food-safety near miss: its own risk assessment and layout.
    assert inc["food_safety_near_miss"] is True
    await hse.put(f"/api/v1/incidents/{iid}/investigation", json={
        "investigator_id": None, "five_whys": ["a", "b", "c", "d", "e"]})
    assert (await hse.post(f"/api/v1/incidents/{iid}/investigation/sign", json=sig)).status_code == 200
    got = (await hse.get(f"/api/v1/incidents/{iid}/investigation")).json()
    # Strings sent the older way are each step's answer (UAT 13, TC-06).
    assert got["five_whys"] == [{"question": "", "answer": x} for x in "abcde"]

    plain, inc = await investigate("near_miss", [])
    assert inc["needs_five_whys"] is False
    assert (await hse.post(f"/api/v1/incidents/{plain}/investigation/sign", json=sig)).status_code == 200


async def test_hse_adds_a_type_without_its_legal_flags(hse_manager, worker, db_session):
    """★ Round 12, GEN-01 and Q10."""
    _, hse = hse_manager
    _, w = worker
    food = await _food(db_session)
    tag = uuid.uuid4().hex[:5]
    made = await hse.post("/api/v1/settings/incident-types", json={
        "label": f"Allergen cross-contact {tag}", "form_kind": "near_miss",
        "category_id": str(food), "restricted": False})
    assert made.status_code == 201, made.text
    code = made.json()["code"]
    assert code.startswith("custom_")

    offered = {t["code"]: t for t in (await w.get(
        "/api/v1/incidents/types", params={"form_kind": "near_miss"})).json()}
    assert code in offered and offered[code]["asks_injury_class"] is False

    filed = await w.post("/api/v1/incidents", json=_near_miss(code))
    assert filed.status_code == 201, filed.text
    row = await db_session.get(Incident, uuid.UUID(filed.json()["id"]))
    assert (row.category_id, row.mol_reportable, row.injury_class) == (food, False, None)
    assert filed.json()["incident_type_label"] == f"Allergen cross-contact {tag}"

    builtin = await hse.patch("/api/v1/settings/incident-types/fatality", json={"label": "Death"})
    assert builtin.status_code == 409, "the built-in types are fixed"

    restricted = await hse.patch(f"/api/v1/settings/incident-types/{code}", json={"restricted": True})
    assert restricted.json()["restricted"] is True
    assert (await w.post("/api/v1/incidents", json=_near_miss(code))).status_code == 403

    retired = await hse.patch(f"/api/v1/settings/incident-types/{code}", json={"is_active": False})
    assert retired.json()["is_active"] is False
    assert code not in {t["code"] for t in (await hse.get("/api/v1/incidents/types")).json()}
    assert (await hse.post("/api/v1/incidents", json=_near_miss(code))).status_code == 422
    # The incident that named it still reads.
    again = (await hse.get(f"/api/v1/incidents/{filed.json()['id']}")).json()
    assert again["incident_type_label"] == f"Allergen cross-contact {tag}"

    listed = {t["code"]: t for t in (await hse.get("/api/v1/settings/incident-types")).json()}
    assert listed["fatality"]["builtin"] and listed["fatality"]["mol_reportable"]
    assert not listed[code]["builtin"] and not listed[code]["is_active"]


async def test_a_rename_keeps_old_reports_and_a_used_type_keeps_its_form(hse_manager, db_session):
    """★ B3/B4 review L2 and L7: HSE renaming their own type rewrote the kind on
    every report that named it; a used type could be moved to another form;
    and a report could be reclassified to a retired type."""
    _, hse = hse_manager
    food = await _food(db_session)
    tag = uuid.uuid4().hex[:5]

    async def add(label):
        made = await hse.post("/api/v1/settings/incident-types", json={
            "label": label, "form_kind": "near_miss", "category_id": str(food),
            "restricted": False})
        assert made.status_code == 201, made.text
        return made.json()["code"]

    code = await add(f"Glass near product {tag}")
    old = (await hse.post("/api/v1/incidents", json=_near_miss(code))).json()
    renamed = await hse.patch(f"/api/v1/settings/incident-types/{code}",
                              json={"label": f"Glass or brittle plastic {tag}"})
    assert renamed.status_code == 200
    assert (await hse.get(f"/api/v1/incidents/{old['id']}")).json()["incident_type_label"] \
        == f"Glass near product {tag}", "★ the old report keeps what it was filed as"
    new = (await hse.post("/api/v1/incidents", json=_near_miss(code))).json()
    assert new["incident_type_label"] == f"Glass or brittle plastic {tag}"

    moved = await hse.patch(f"/api/v1/settings/incident-types/{code}", json={"form_kind": "medical"})
    assert moved.status_code == 409, "★ reports already use it"
    unused = await add(f"Unused {tag}")
    assert (await hse.patch(f"/api/v1/settings/incident-types/{unused}",
                            json={"form_kind": "medical"})).status_code == 200, \
        "an unused type can still move"

    spare = await add(f"Spare {tag}")
    await hse.patch(f"/api/v1/settings/incident-types/{spare}", json={"is_active": False})
    await hse.post(f"/api/v1/incidents/{new['id']}/submit")
    refused = await hse.post(f"/api/v1/incidents/{new['id']}/reclassify",
                             json={"incident_type": spare})
    assert refused.status_code == 422 and "retired" in refused.text
