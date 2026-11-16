"""UAT 14 TC-10: injury classification and the two near misses in Reports."""
from tests.test_reporting_search import _area, _counts, _incident


async def test_a_typed_incident_with_no_class_reads_no_injury(hse_manager, db_session):
    """"Why is INC-2026-0137 labeled as an injury classification of not
    recorded, when it is a food safety near miss." Blank is how the form says
    nobody was hurt; only an incident with no type at all is not recorded."""
    _, client = hse_manager
    area = _area()
    fa = await _incident(db_session, area=area, kind="first_aid")
    fa.injury_class = "first_aid"
    lt = await _incident(db_session, area=area, kind="first_aid")
    lt.injury_class = "lost_time"
    await _incident(db_session, area=area, kind="near_miss_food_safety", form="near_miss")
    await _incident(db_session, area=area, kind="property_damage", form="equipment")
    await _incident(db_session, area=area, kind=None)
    await db_session.flush()

    got = await client.get("/api/v1/reports/group", params={
        "entity": "incident", "dimension": "injury_class", "filter": f"location:{area}"})
    assert got.status_code == 200, got.text
    assert _counts(got.json()) == {"First Aid": 1, "Lost Time": 1, "No injury": 2,
                                   "Not recorded": 1}

    # The bar opens to exactly its incidents.
    opened = await client.get("/api/v1/reports/group", params=[
        ("entity", "incident"), ("dimension", "incident_type"),
        ("filter", f"location:{area}"), ("filter", "injury_class:none")])
    assert sum(r["count"] for r in opened.json()) == 2


async def test_the_two_near_misses_read_differently(hse_manager, db_session):
    """"Near miss is on the list twice": both shortened to "Near Miss"."""
    _, client = hse_manager
    area = _area()
    await _incident(db_session, area=area, kind="near_miss", form="near_miss")
    await _incident(db_session, area=area, kind="near_miss_food_safety", form="near_miss")
    await _incident(db_session, area=area, kind="first_aid")
    await db_session.flush()

    got = await client.get("/api/v1/reports/group", params={
        "entity": "incident", "dimension": "incident_type", "filter": f"location:{area}"})
    labels = set(_counts(got.json()))
    assert "Near Miss — Food Safety" in labels
    assert "Near Miss" in labels
    assert "First Aid Injury" in labels   # a name nothing shares stays short
