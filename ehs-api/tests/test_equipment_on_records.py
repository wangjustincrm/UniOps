"""★ Round 10 (open box): the equipment register named on incidents, hazards,
job analyses and permits. A new choice must be in service; a record keeps the
one it names even once that is retired."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.equipment import Equipment

SIGNATURE = "data:image/png;base64,iVBORw0KGgo="


async def _equipment(db, active=True):
    e = Equipment(id=uuid.uuid4(), asset_no=f"EQ-{uuid.uuid4().hex[:5]}", name="Capper",
                  is_active=active)
    db.add(e)
    await db.flush()
    return e


async def test_an_incident_names_equipment_and_the_register_finds_it(hse_manager, db_session):
    _, hse = hse_manager
    eq = await _equipment(db_session)
    made = await hse.post("/api/v1/incidents", json={
        "form_kind": "equipment", "title": "Capper jammed",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "incident_type": "property_damage", "injury_class": "none",
        "signature": SIGNATURE, "equipment_id": str(eq.id)})
    assert made.status_code in (200, 201), made.text
    iid = made.json()["id"]
    assert made.json()["equipment_id"] == str(eq.id)
    assert (await hse.post(f"/api/v1/incidents/{iid}/submit")).status_code == 200
    found = (await hse.get("/api/v1/incidents", params={"equipment_id": str(eq.id)})).json()
    assert [r["id"] for r in found] == [iid]

    # Retired afterwards: the incident keeps it, and can still be edited.
    eq.is_active = False
    await db_session.flush()
    assert (await hse.patch(f"/api/v1/incidents/{iid}", json={
        "title": "Capper jammed on line 2", "equipment_id": str(eq.id)})).status_code == 200
    # But it is not a new choice for anything.
    other = await _equipment(db_session)
    assert (await hse.patch(f"/api/v1/incidents/{iid}", json={
        "equipment_id": str(other.id)})).status_code == 200
    back = await hse.patch(f"/api/v1/incidents/{iid}", json={"equipment_id": str(eq.id)})
    assert back.status_code == 422 and "retired" in back.text
    assert (await hse.patch(f"/api/v1/incidents/{iid}", json={
        "equipment_id": str(uuid.uuid4())})).status_code == 422


async def test_hazards_and_job_analyses_refuse_retired_equipment(hse_manager, db_session):
    _, hse = hse_manager
    retired = await _equipment(db_session, active=False)
    live = await _equipment(db_session)
    bad = await hse.post("/api/v1/hazards", json={"title": "Guard missing",
                                                  "equipment_id": str(retired.id)})
    assert bad.status_code == 422, bad.text
    good = await hse.post("/api/v1/hazards", json={"title": "Guard missing",
                                                   "equipment_id": str(live.id)})
    assert good.status_code in (200, 201), good.text
    assert good.json()["equipment_id"] == str(live.id)
    jsa = await hse.post("/api/v1/jsa", json={"title": "Change the capper heads",
                                                      "equipment_id": str(retired.id)})
    assert jsa.status_code == 422, jsa.text


async def test_editing_keeps_what_the_form_does_not_send(hse_manager, db_session):
    """★ The form sends asset number, name, kind and area. Everything else on
    the record came from the import, and an edit must leave it alone."""
    _, client = hse_manager
    e = await _equipment(db_session)
    e.manufacturer, e.model, e.serial_no, e.description = "Krones", "K-200", "SN1", "Main capper"
    e.is_active = False
    await db_session.flush()

    saved = await client.patch(f"/api/v1/equipment/{e.id}", json={
        "asset_no": e.asset_no, "name": "Capper 2", "equipment_type": "Capper",
        "location_id": None})
    assert saved.status_code == 200, saved.text
    await db_session.refresh(e)
    assert e.name == "Capper 2", "the edit itself must land"
    assert (e.manufacturer, e.model, e.serial_no, e.description) == (
        "Krones", "K-200", "SN1", "Main capper")
    assert e.is_active is False, "an edit must not put a retired machine back in service"
