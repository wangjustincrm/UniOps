"""UAT 13, the Food Safety Risk Assessment and its register (R13-B)."""
import pytest

from app.services import food_safety as fs
from tests.conftest import authed_client, make_token, make_user
from tests.test_incident_types_r12 import _food, _near_miss


@pytest.mark.parametrize("p,s,d,ccp,rpn,band", [
    (1, 1, 1, False, 1, "low"),
    (3, 3, 1, False, 9, "low"),
    (2, 5, 1, False, 10, "medium"),
    (2, 3, 4, False, 24, "medium"),
    (5, 5, 1, False, 25, "high"),
    (3, 3, 5, False, 45, "critical"),
    (5, 5, 5, False, 125, "critical"),
    # "if the CCP failure is checked, automatically set the RPN to 46"
    (1, 1, 1, True, 46, "critical"),
])
def test_rpn_and_bands_as_hse_wrote_them(p, s, d, ccp, rpn, band):
    assert fs.rpn(p, s, d, ccp) == rpn
    assert fs.band(rpn) == band


async def _food_near_miss(client, db_session, code="near_miss_food_safety"):
    await _food(db_session)
    made = (await client.post("/api/v1/incidents", json=_near_miss(code))).json()
    await client.post(f"/api/v1/incidents/{made['id']}/submit")
    return made


async def test_assessed_on_a_food_safety_near_miss_only(hse_manager, db_session):
    _, hse = hse_manager
    food = await _food_near_miss(hse, db_session)
    plain = await _food_near_miss(hse, db_session, code="near_miss")
    assert (await hse.get(f"/api/v1/incidents/{food['id']}")).json()["food_safety_near_miss"] is True
    assert (await hse.get(f"/api/v1/incidents/{plain['id']}")).json()["food_safety_near_miss"] is False

    made = await hse.post(f"/api/v1/incidents/{food['id']}/food-safety-assessments",
                          json={"probability": 3, "severity": 4, "detectability": 2})
    assert made.status_code == 201, made.text
    assert (made.json()["rpn"], made.json()["band"]) == (24, "medium")
    ccp = await hse.post(f"/api/v1/incidents/{food['id']}/food-safety-assessments",
                         json={"probability": 1, "severity": 1, "detectability": 1, "ccp_failure": True})
    assert (ccp.json()["rpn"], ccp.json()["band"]) == (46, "critical")
    listed = (await hse.get(f"/api/v1/incidents/{food['id']}/food-safety-assessments")).json()
    assert [r["rpn"] for r in listed] == [46, 24]  # latest first

    refused = await hse.post(f"/api/v1/incidents/{plain['id']}/food-safety-assessments",
                             json={"probability": 1, "severity": 1, "detectability": 1})
    assert refused.status_code == 409
    out_of_range = await hse.post(f"/api/v1/incidents/{food['id']}/food-safety-assessments",
                                  json={"probability": 6, "severity": 1, "detectability": 1})
    assert out_of_range.status_code == 422


async def test_assessing_is_for_those_who_investigate(hse_manager, worker, test_engine, db_session):
    _, hse = hse_manager
    _, emp = worker
    food = await _food_near_miss(hse, db_session)
    body = {"probability": 2, "severity": 2, "detectability": 2}
    assert (await emp.post(f"/api/v1/incidents/{food['id']}/food-safety-assessments",
                           json=body)).status_code == 403
    sup = await make_user(test_engine, role="area_supervisor")
    async with authed_client(make_token(sup.id, sup.role), db_session) as s:
        assert (await s.post(f"/api/v1/incidents/{food['id']}/food-safety-assessments",
                             json=body)).status_code == 201
    # The register is the incident register's to read.
    assert (await emp.get("/api/v1/food-safety/register")).status_code == 403


async def test_the_register_lists_each_near_miss_by_its_latest_assessment(hse_manager, db_session):
    _, hse = hse_manager
    low = await _food_near_miss(hse, db_session)
    high = await _food_near_miss(hse, db_session)
    url = "/api/v1/incidents/{}/food-safety-assessments"
    await hse.post(url.format(low["id"]), json={"probability": 5, "severity": 5, "detectability": 5})
    await hse.post(url.format(low["id"]), json={"probability": 1, "severity": 2, "detectability": 1})
    await hse.post(url.format(high["id"]), json={"probability": 1, "severity": 1, "detectability": 1,
                                                   "ccp_failure": True})
    page = (await hse.get("/api/v1/food-safety/register", params={"page_size": 200})).json()
    mine = {r["incident_id"]: r for r in page["items"] if r["incident_id"] in (low["id"], high["id"])}
    assert mine[low["id"]]["rpn"] == 2 and mine[high["id"]]["rpn"] == 46
    order = [r["incident_id"] for r in page["items"] if r["incident_id"] in mine]
    assert order == [high["id"], low["id"]]  # highest RPN first
    critical = (await hse.get("/api/v1/food-safety/register", params={"band": "critical"})).json()
    assert high["id"] in {r["incident_id"] for r in critical["items"]}
    assert low["id"] not in {r["incident_id"] for r in critical["items"]}
    assert (await hse.get("/api/v1/food-safety/register", params={"sort": "nope"})).status_code == 422
    scales = (await hse.get("/api/v1/food-safety/scales")).json()
    assert len(scales["probability"]) == 5 and scales["ccp_rpn"] == 46
