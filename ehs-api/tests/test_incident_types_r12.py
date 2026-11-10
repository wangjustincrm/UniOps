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
