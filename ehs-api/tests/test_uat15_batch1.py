"""UAT 15, first batch: TC-05, TC-15, and the answers K, L, M."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.crud import toolbox as tbt
from app.models.config import EhsConfig
from app.models.incident import Incident
from app.services import statutory
from app.services.day_range import plant_today
from tests.conftest import authed_client, make_token, make_user
from tests.test_food_safety_r13b import _food_near_miss
from tests.test_incident_types_r12 import _near_miss


@pytest.fixture(autouse=True)
async def _config(db_session):
    if await db_session.get(EhsConfig, 1) is None:
        db_session.add(EhsConfig(id=1))
        await db_session.commit()


async def _area(db, name):
    lid = uuid.uuid4()
    await db.execute(text("insert into locations (id, code, name, level, path, depth, is_active)"
                          " values (:i, :c, :n, 'area', :n, 0, true)"),
                     {"i": lid, "c": f"L{uuid.uuid4().hex[:5]}", "n": name})
    await db.commit()
    return lid


# ── TC-05 ───────────────────────────────────────────────────────────────────
async def test_tc05_the_area_that_completed_the_work(hse_manager, db_session):
    _, hse = hse_manager
    area = await _area(db_session, "Production/Sanitation")
    food = await _food_near_miss(hse, db_session)
    url = f"/api/v1/incidents/{food['id']}/causes"
    cause = {"cause_type": "immediate", "label": "Lid left off",
             "completed_by_location_id": str(area), "completed_on": "2026-11-10"}
    got = await hse.put(url, json={"causes": [cause]})
    assert got.status_code == 200, got.text
    c = got.json()["immediate"][0]
    assert (c["completed_by_location_id"], c["completed_by_location_path"]) == (str(area), "Production/Sanitation")
    # Renamed later: the cause keeps the path it was recorded with.
    await db_session.execute(text("update locations set path = 'Production/Hygiene' where id = :i"), {"i": area})
    await db_session.commit()
    again = (await hse.put(url, json={"causes": [cause]})).json()
    assert again["immediate"][0]["completed_by_location_path"] == "Production/Sanitation"
    bad = await hse.put(url, json={"causes": [{**cause, "completed_by_location_id": str(uuid.uuid4())}]})
    assert bad.status_code == 422


# ── TC-15 ───────────────────────────────────────────────────────────────────
async def test_tc15_ppe_on_a_hazard(hse_manager, db_session):
    _, hse = hse_manager
    await db_session.execute(text(
        "insert into ehs_vocabularies (code, name) values ('ppe', 'PPE') on conflict (code) do nothing"))
    item = uuid.uuid4()
    await db_session.execute(text(
        "insert into ehs_vocabulary_items (id, vocabulary_code, code, label, sort_order, is_active, attrs, path)"
        " values (:i, 'ppe', :c, 'Cut-resistant gloves', 1, true, '{}'::jsonb, 'Cut-resistant gloves')"),
        {"i": item, "c": f"P{uuid.uuid4().hex[:5]}"})
    await db_session.commit()
    made = await hse.post("/api/v1/hazards", json={"title": "Blade", "hazard_ppe_ids": [str(item)]})
    assert made.status_code in (200, 201), made.text
    assert made.json()["hazard_ppe"] == [{"id": str(item), "label": "Cut-resistant gloves"}]
    refused = await hse.patch(f"/api/v1/hazards/{made.json()['id']}", json={"hazard_ppe_ids": [str(uuid.uuid4())]})
    assert refused.status_code in (404, 422)


# ── M ───────────────────────────────────────────────────────────────────────
async def test_m_only_the_batch_code_keeps_the_hse_review(hse_manager, db_session):
    _, hse = hse_manager
    made = (await hse.post("/api/v1/incidents", json=_near_miss("near_miss"))).json()
    iid = made["id"]
    await hse.post(f"/api/v1/incidents/{iid}/submit")
    inv = {"sequence_of_events": "Lid left off the blender"}
    causes = {"causes": [{"cause_type": "immediate", "label": "No changeover check"}]}
    assert (await hse.put(f"/api/v1/incidents/{iid}/investigation", json=inv)).status_code == 200
    assert (await hse.put(f"/api/v1/incidents/{iid}/causes", json=causes)).status_code == 200

    async def review():
        row = await db_session.get(Incident, uuid.UUID(iid))
        row.hse_reviewed_at = datetime.now(timezone.utc)
        await db_session.commit()

    async def reviewed() -> bool:
        await db_session.commit()
        return (await hse.get(f"/api/v1/incidents/{iid}")).json()["hse_reviewed_at"] is not None

    await review()
    # The investigation page's save: the batch code, then everything as it was.
    assert (await hse.patch(f"/api/v1/incidents/{iid}", json={"batch_code": "B-2211"})).status_code == 200
    assert (await hse.put(f"/api/v1/incidents/{iid}/investigation", json=inv)).status_code == 200
    assert (await hse.put(f"/api/v1/incidents/{iid}/causes", json=causes)).status_code == 200
    assert await reviewed()
    # A change to the investigation itself still sends it back for review.
    await hse.put(f"/api/v1/incidents/{iid}/investigation", json={"sequence_of_events": "Something else"})
    assert not await reviewed()
    await review()
    await hse.put(f"/api/v1/incidents/{iid}/causes", json={"causes": []})
    assert not await reviewed()


# ── L ───────────────────────────────────────────────────────────────────────
async def test_l_recording_the_filing_closes_every_role_s_task(db_session):
    from app.services import deadlines as deadline_service
    from app.tasks import scheduler
    from tests.test_scheduler import _deadline
    now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    d = await _deadline(db_session, due_at=now + timedelta(hours=20))
    await scheduler.sweep_statutory(db_session, now)
    open_ = lambda: db_session.execute(text(
        "select assigned_role from tasks where document_id = :d and is_completed = false"),
        {"d": str(d.source_id)})
    assert sorted(r[0] for r in (await open_()).all()) == ["ehs_coordinator", "ehs_manager"]
    await deadline_service.satisfy(db_session, d, user_id=None, user_name="Cora", evidence_file_id=None,
                                   evidence_note="filed", when=now)
    await db_session.flush()
    assert (await open_()).all() == []


# ── K ───────────────────────────────────────────────────────────────────────
async def test_k_a_talk_held_again_counts_in_the_month_it_was_held(hse_manager, test_engine, db_session):
    _, hse = hse_manager
    sup = await make_user(test_engine, role="area_supervisor", full_name=f"Kay {uuid.uuid4().hex[:4]}")
    august = date(2041, 8, 1)
    talk = (await hse.post("/api/v1/toolbox-talks", json={"title": "Ladders",
                                                          "period_month": august.isoformat()})).json()
    first = (await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/publish",
                            json={"supervisor_ids": [str(sup.id)]})).json()[0]
    async with authed_client(make_token(sup.id, sup.role), db_session) as k:
        held = await k.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again", json={
            "signatures": [{"person_name": "Priya", "signature": "data:image/png;base64,AA"}],
            "held_at": "2041-11-12T15:00:00Z"})
        assert held.status_code == 201, held.text

    async def row(month):
        page = (await hse.get("/api/v1/toolbox-talks/tracker",
                              params={"month": month, "q": sup.full_name})).json()
        return page["items"][0] if page["items"] else None
    aug, nov = await row("2041-08-01"), await row("2041-11-01")
    assert (aug["assigned"], aug["delivered"]) == (1, 0)        # the hand-out, still to deliver
    assert (nov["assigned"], nov["delivered"]) == (1, 1)        # the talk held again, in November
    done = (await hse.get(f"/api/v1/toolbox-talks/{talk['id']}/completion")).json()
    assert (done["total"], done["delivered"]) == (1, 0)          # the hand-outs only
    assert first["id"]
