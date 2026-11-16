"""UAT 13: the equipment picker offers the Systems of the area chosen (R13-C)."""

async def test_the_systems_offered_are_the_areas_own(hse_manager, db_session):
    """"When picking production/104 - packaging/ line 1 large can the systems
    available should not include any systems or equipment that is not in that
    area … I should not see an agitator system." """
    from tests.test_equipment_register import _area, _machine, _system, _tag
    _, hse = hse_manager
    root = f"Plant {_tag()}"
    pack, line1 = await _area(db_session, f"{root}/104 Packaging"), await _area(db_session, f"{root}/104 Packaging/Line 1")
    pre = await _area(db_session, f"{root}/102 Pretreatment")
    filler, agitator, loose = (await _system(db_session, f"Filler {_tag()}"),
                               await _system(db_session, f"Agitator {_tag()}"),
                               await _system(db_session, f"Loose {_tag()}"))
    await _machine(db_session, "Filler 1", location_id=line1, system=filler)
    await _machine(db_session, "Agitator A", location_id=pre, system=agitator)
    await _machine(db_session, "Somewhere", location_id=None, system=loose)
    await db_session.commit()

    def labels(r):
        return {s["label"] for s in r.json()["systems"]}
    near = await hse.get("/api/v1/equipment/summary", params={"near_location_id": str(line1)})
    assert near.status_code == 200, near.text
    assert filler.label in labels(near)
    assert agitator.label not in labels(near) and loose.label not in labels(near)
    # The whole register still has them all.
    everything = labels(await hse.get("/api/v1/equipment/summary"))
    assert {filler.label, agitator.label, loose.label} <= everything
    # An area above Line 1 offers what is below it.
    assert filler.label in labels(await hse.get("/api/v1/equipment/summary",
                                                params={"near_location_id": str(pack)}))
