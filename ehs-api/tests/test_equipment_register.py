"""★ Round 12, batch 2: the equipment register.

EQ-01 "does not show all the equipment" (200 of 7,170, no total); EQ-03 the
System as a list HSE maintain; Q3 retire, and delete only what nothing names;
INC-10/11 a picker narrowed to where the record was filed; EQ-04 a template and
an upload that matches areas on their path.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.models.equipment import Equipment
from app.models.vocabulary import Vocabulary, VocabularyItem

SIGNATURE = "data:image/png;base64,iVBORw0KGgo="


def _tag() -> str:
    return uuid.uuid4().hex[:6]


async def _systems(db):
    if await db.get(Vocabulary, "equipment_system") is None:
        db.add(Vocabulary(code="equipment_system", name="Equipment System"))
        await db.flush()


async def _system(db, label):
    await _systems(db)
    item = VocabularyItem(id=uuid.uuid4(), vocabulary_code="equipment_system",
                          code=f"S_{_tag()}", label=label, path=label)
    db.add(item)
    await db.flush()
    return item


async def _area(db, path, name=None):
    lid = uuid.uuid4()
    await db.execute(text(
        "insert into locations (id, code, name, level, path, depth, is_active)"
        " values (:id, :code, :name, 'area', :path, :depth, true)"),
        {"id": lid, "code": f"L-{str(lid)[:8]}", "name": name or path.split("/")[-1],
         "path": path, "depth": path.count("/")})
    return lid


async def _machine(db, name, *, location_id=None, system=None, active=True):
    e = Equipment(id=uuid.uuid4(), asset_no=f"EQ-{_tag()}", name=name,
                  location_id=location_id, system_id=system.id if system else None,
                  is_active=active)
    db.add(e)
    await db.flush()
    return e


async def test_a_page_carries_the_total_it_is_a_page_of(hse_manager, db_session):
    _, client = hse_manager
    tag = _tag()
    for i in range(7):
        await _machine(db_session, f"Pump {tag} {i}")
    await db_session.commit()

    first = (await client.get("/api/v1/equipment",
                              params={"search": tag, "limit": 3})).json()
    assert first["total"] == 7, "the register must say how many there are, not how many it sent"
    assert len(first["items"]) == 3
    last = (await client.get("/api/v1/equipment",
                             params={"search": tag, "limit": 3, "offset": 6})).json()
    assert [r["name"] for r in last["items"]] == [f"Pump {tag} 6"]


async def test_system_is_a_list_entry_shown_by_its_current_name(hse_manager, db_session):
    """★ The exception to the snapshot rule: renaming a System renames it on
    every machine, because merging 148 kinds is what HSE asked to do."""
    _, client = hse_manager
    tag = _tag()
    valve = await _system(db_session, f"Valve {tag}")
    m = await _machine(db_session, f"V-1 {tag}", system=valve)
    await db_session.commit()

    page = (await client.get("/api/v1/equipment",
                             params={"system_id": str(valve.id)})).json()
    assert [r["id"] for r in page["items"]] == [str(m.id)]
    assert page["items"][0]["system_label"] == f"Valve {tag}"

    renamed = await client.patch(f"/api/v1/settings/vocabularies/equipment_system/items/{valve.id}",
                                 json={"label": f"Valves {tag}"})
    assert renamed.status_code == 200, renamed.text
    got = (await client.get(f"/api/v1/equipment/{m.id}")).json()
    assert got["system_label"] == f"Valves {tag}"

    usage = (await client.get(
        f"/api/v1/settings/vocabularies/equipment_system/items/{valve.id}/usage")).json()
    assert usage["total"] == 1, "Lists must know machines use the entry"


async def test_summary_counts_in_service_machines_by_system(hse_manager, db_session):
    _, client = hse_manager
    tag = _tag()
    pump = await _system(db_session, f"Pump {tag}")
    for _ in range(3):
        await _machine(db_session, f"P {tag}", system=pump)
    await _machine(db_session, f"P old {tag}", system=pump, active=False)
    await db_session.commit()

    rows = {r["label"]: r["count"] for r in (await client.get(
        "/api/v1/equipment/summary")).json()["systems"]}
    assert rows[f"Pump {tag}"] == 3, "a retired machine is not counted in its System"


async def test_merging_a_system_moves_its_machines_and_retires_it(hse_manager, db_session):
    _, client = hse_manager
    tag = _tag()
    a = await _system(db_session, f"Ctrl valve {tag}")
    b = await _system(db_session, f"Valve {tag}")
    m1 = await _machine(db_session, f"CV1 {tag}", system=a)
    m2 = await _machine(db_session, f"CV2 {tag}", system=a)
    await db_session.commit()

    merged = await client.post(f"/api/v1/equipment/systems/{a.id}/merge",
                               json={"into_id": str(b.id)})
    assert merged.status_code == 200 and merged.json()["moved"] == 2, merged.text
    for m in (m1, m2):
        await db_session.refresh(m)
        assert m.system_id == b.id
    await db_session.refresh(a)
    assert a.is_active is False
    again = await client.post(f"/api/v1/equipment/systems/{b.id}/merge",
                              json={"into_id": str(a.id)})
    assert again.status_code == 422, "nothing is merged into a retired System"


async def test_retire_hides_from_pickers_and_delete_only_what_nothing_names(hse_manager, db_session):
    _, client = hse_manager
    tag = _tag()
    named = await _machine(db_session, f"Capper {tag}")
    spare = await _machine(db_session, f"Spare {tag}")
    await db_session.commit()
    made = await client.post("/api/v1/incidents", json={
        "form_kind": "equipment", "title": "Capper jammed",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "signature": SIGNATURE, "equipment_id": str(named.id)})
    assert made.status_code == 201, made.text

    refused = await client.delete(f"/api/v1/equipment/{named.id}")
    assert refused.status_code == 409 and "1 incidents" in refused.text, refused.text

    retired = await client.post(f"/api/v1/equipment/{named.id}/retire")
    assert retired.status_code == 200 and retired.json()["is_active"] is False
    assert retired.json()["retired_at"] and retired.json()["retired_by_name"]
    live = (await client.get("/api/v1/equipment", params={"search": tag})).json()
    assert [r["name"] for r in live["items"]] == [f"Spare {tag}"]
    gone = (await client.get("/api/v1/equipment",
                             params={"search": tag, "state": "retired"})).json()
    assert [r["name"] for r in gone["items"]] == [f"Capper {tag}"]
    # The record still shows the machine it names.
    shown = (await client.get("/api/v1/equipment/lookup",
                              params={"ids": [str(named.id)]})).json()
    assert shown[0]["name"] == f"Capper {tag}" and shown[0]["is_active"] is False

    assert (await client.delete(f"/api/v1/equipment/{spare.id}")).status_code == 204
    assert (await client.get(f"/api/v1/equipment/{spare.id}")).status_code == 404

    back = await client.post(f"/api/v1/equipment/{named.id}/reinstate")
    assert back.json()["is_active"] is True and back.json()["retired_at"] is None


async def test_picker_offers_the_machines_where_the_record_was_filed(hse_manager, db_session):
    """INC-11: at the WWTP, the WWTP's machines — not the warehouse conveyor.
    A room with none of its own offers its parent's; a machine with no area is
    offered everywhere."""
    _, client = hse_manager
    tag = _tag()
    wwtp = await _area(db_session, f"Plant {tag}/WWTP")
    room = await _area(db_session, f"Plant {tag}/WWTP/Blower room")
    warehouse = await _area(db_session, f"Plant {tag}/Warehouse")
    empty = await _area(db_session, f"Elsewhere {tag}")
    await _machine(db_session, f"Aerator {tag}", location_id=wwtp)
    await _machine(db_session, f"Conveyor 105 {tag}", location_id=warehouse)
    await _machine(db_session, f"Ladder {tag}")  # no area
    await db_session.commit()

    def names(page):
        return sorted(r["name"] for r in page["items"])

    at_wwtp = (await client.get("/api/v1/equipment", params={
        "search": tag, "near_location_id": str(wwtp)})).json()
    assert names(at_wwtp) == [f"Aerator {tag}", f"Ladder {tag}"]
    assert at_wwtp["scope_path"] == f"Plant {tag}/WWTP"

    in_room = (await client.get("/api/v1/equipment", params={
        "search": tag, "near_location_id": str(room)})).json()
    assert names(in_room) == [f"Aerator {tag}", f"Ladder {tag}"]
    assert in_room["scope_path"] == f"Plant {tag}/WWTP", "nothing in the room: its parent's"

    nowhere = (await client.get("/api/v1/equipment", params={
        "search": tag, "near_location_id": str(empty)})).json()
    assert names(nowhere) == [f"Ladder {tag}"] and nowhere["scope_path"] is None


async def test_import_matches_areas_on_their_path_and_adds_new_systems(hse_manager, db_session):
    _, client = hse_manager
    tag = _tag()
    line_a = await _area(db_session, f"Dryer {tag}/Line 1", name=f"Line 1 {tag}")
    await _area(db_session, f"Evaporator {tag}/Line 1", name=f"Line 1 {tag}")
    only = await _area(db_session, f"Lab {tag}", name=f"Lab {tag}")
    await _systems(db_session)
    await db_session.commit()

    template = await client.get("/api/v1/equipment/template.csv")
    assert template.status_code == 200
    header = template.text.lstrip("﻿").splitlines()[0]
    assert header.startswith("asset_no,name,description,system,area")

    csv = (
        "asset_no,name,description,system,area\n"
        f"A{tag}1,Fan {tag},Exhaust fan,Fan {tag},Dryer {tag}/Line 1\n"
        f"A{tag}2,Pump {tag},,Fan {tag},Line 1 {tag}\n"
        f"A{tag}3,Scale {tag},,,Lab {tag}\n"
    )
    up = await client.post("/api/v1/equipment/import",
                           files={"file": ("e.csv", csv.encode(), "text/csv")})
    body = up.json()
    assert up.status_code == 200, up.text
    assert body["created"] == 3 and body["new_systems"] == [f"Fan {tag}"]
    assert any("2 areas are called" in p for p in body["problems"]), body["problems"]

    rows = {e.name: e for e in (await db_session.execute(
        select(Equipment).where(Equipment.name.like(f"%{tag}")))).scalars()}
    assert rows[f"Fan {tag}"].location_id == line_a
    assert rows[f"Fan {tag}"].description == "Exhaust fan"
    assert rows[f"Pump {tag}"].location_id is None, "an ambiguous name is reported, not guessed"
    assert rows[f"Scale {tag}"].location_id == only, "a name only one area has still matches"
    assert rows[f"Fan {tag}"].system_id == rows[f"Pump {tag}"].system_id is not None
