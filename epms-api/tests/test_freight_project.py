"""Project-related freight shipments (FRT-019).

A shipment ticked "Project related" carries project details instead of a
department / cost center. The invoice line that bills it is pre-filled with
the project-freight cost center (Freight → Carriers setting, R&D by default)
and stays a decision for the matcher until they confirm or change it.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.config import CompanyConfig
from app.models.cost_center import CostCenter
from app.models.department import Department

from tests.test_freight import _no_outbound_calls, _register, _ups_invoice, world  # noqa: F401

SHIP_NO = "1Z4E4F776832551028"


@pytest.fixture
async def rd(test_engine, admin_client, world):  # noqa: F811
    """An R&D cost center set as the project-freight default."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    tag = uuid.uuid4().hex[:6].upper()
    async with factory() as db:
        if (await db.execute(select(CompanyConfig).limit(1))).scalars().first() is None:
            db.add(CompanyConfig())
        dept = Department(code=f"RD{tag}", name=f"R&D {tag}")
        db.add(dept)
        await db.flush()
        cc = CostCenter(code=f"RD-{tag}", name="RD-R&D", department_id=dept.id)
        db.add(cc)
        await db.commit()
        cc_id = cc.id
    r = await admin_client.put("/api/v1/freight/settings", json={"project_cost_center_id": str(cc_id)})
    assert r.status_code == 200, r.text
    assert r.json()["project_cost_center_code"] == f"RD-{tag}"
    return cc_id


async def _register_project(client, w, tracking=SHIP_NO, info="Dryer 3 retrofit — PRJ-2026-014"):
    return await client.post("/api/v1/freight/shipments", json={
        "carrier_id": w["carrier"], "tracking_no": tracking, "is_project": True, "project_info": info})


async def test_project_shipment_needs_details_and_no_cost_center(admin_client, world):  # noqa: F811
    r = await admin_client.post("/api/v1/freight/shipments", json={
        "carrier_id": world["carrier"], "tracking_no": SHIP_NO, "is_project": True, "project_info": "  "})
    assert r.status_code == 422 and "project details" in r.json()["detail"]
    r = await admin_client.post("/api/v1/freight/shipments", json={
        "carrier_id": world["carrier"], "tracking_no": SHIP_NO, "is_project": True,
        "project_info": "X", "cost_center_id": str(world["qa_lab"])})
    assert r.status_code == 422
    # Not a project → the cost center is still required.
    r = await admin_client.post("/api/v1/freight/shipments", json={
        "carrier_id": world["carrier"], "tracking_no": SHIP_NO})
    assert r.status_code == 422 and "cost center" in r.json()["detail"]

    r = await _register_project(admin_client, world)
    assert r.status_code == 201, r.text
    s = r.json()
    assert (s["is_project"], s["project_info"], s["cost_center_id"], s["department_id"]) == \
        (True, "Dryer 3 retrofit — PRJ-2026-014", None, None)
    listed = (await admin_client.get(f"/api/v1/freight/shipments?search={SHIP_NO}")).json()["items"]
    assert listed[0]["is_project"] is True
    # Sorting by Department / Cost Center still works with a project row in the page.
    assert (await admin_client.get("/api/v1/freight/shipments?sort=department")).status_code == 200


async def test_edit_switches_between_project_and_cost_center(admin_client, world):  # noqa: F811
    s = (await _register(admin_client, world, SHIP_NO, "qa_lab")).json()
    url = f"/api/v1/freight/shipments/{s['id']}"
    assert (await admin_client.patch(url, json={"is_project": True})).status_code == 422
    r = await admin_client.patch(url, json={"is_project": True, "project_info": "Line 2 trial"})
    assert r.status_code == 200, r.text
    assert (r.json()["is_project"], r.json()["cost_center_id"]) == (True, None)
    # Changing only the details keeps it a project.
    r = await admin_client.patch(url, json={"project_info": "Line 2 trial — phase B"})
    assert r.json()["project_info"] == "Line 2 trial — phase B"
    assert (await admin_client.patch(url, json={"is_project": False})).status_code == 422
    r = await admin_client.patch(url, json={"is_project": False, "cost_center_id": str(world["sc_sell"])})
    assert r.status_code == 200, r.text
    assert (r.json()["is_project"], r.json()["project_info"], r.json()["cost_center_id"]) == \
        (False, None, str(world["sc_sell"]))


async def test_invoice_line_is_prefilled_and_must_be_confirmed(admin_client, world, rd):  # noqa: F811
    await _register_project(admin_client, world)
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    ship, pick1 = inv["lines"][0], inv["lines"][1]
    assert (ship["match_status"], ship["cost_center_id"], ship["shipment_project_info"]) == \
        ("project", str(rd), "Dryer 3 retrofit — PRJ-2026-014")
    # The pickup on the same day follows the pre-filled cost center.
    assert (pick1["linked_line_id"], pick1["cost_center_id"]) == (ship["id"], str(rd))
    assert any("project-related" in b for b in inv["finalize_blockers"])
    assert (await admin_client.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")).status_code == 422

    line_url = f"/api/v1/freight/invoices/{inv['id']}/lines/{ship['id']}"
    # Correcting the amount alone is not a confirmation.
    r = await admin_client.patch(line_url, json={"amount": "51.28"})
    assert r.json()["lines"][0]["match_status"] == "project"
    # Re-running auto-match keeps it waiting.
    r = await admin_client.post(f"/api/v1/freight/invoices/{inv['id']}/auto-match")
    assert r.json()["lines"][0]["match_status"] == "project"
    # Confirming the pre-filled R&D cost center is the decision.
    r = await admin_client.patch(line_url, json={"cost_center_id": str(rd)})
    assert r.json()["lines"][0]["match_status"] == "manual"
    assert r.json()["finalize_blockers"] == []
    assert (await admin_client.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")).status_code == 200
    s = (await admin_client.get(f"/api/v1/freight/shipments?search={SHIP_NO}")).json()["items"][0]
    # Billed, still a project shipment — the cost center lives on the invoice line.
    assert (s["status"], s["is_project"], s["cost_center_id"]) == ("billed", True, None)


async def test_matcher_may_choose_another_cost_center(admin_client, world, rd):  # noqa: F811
    await _register_project(admin_client, world)
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    ship = inv["lines"][0]
    r = await admin_client.patch(f"/api/v1/freight/invoices/{inv['id']}/lines/{ship['id']}",
                                 json={"cost_center_id": str(world["sc_sell"])})
    line = r.json()["lines"][0]
    assert (line["match_status"], line["cost_center_id"]) == ("manual", str(world["sc_sell"]))
    # The pickup that follows it moves with it.
    assert r.json()["lines"][1]["cost_center_id"] == str(world["sc_sell"])


async def test_no_project_default_leaves_the_line_empty(test_engine, admin_client, world):  # noqa: F811
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        if (await db.execute(select(CompanyConfig).limit(1))).scalars().first() is None:
            db.add(CompanyConfig())
            await db.commit()
    r = await admin_client.put("/api/v1/freight/settings", json={"project_cost_center_id": None})
    assert r.status_code == 200 and r.json()["project_cost_center_id"] is None
    await _register_project(admin_client, world)
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    ship = inv["lines"][0]
    assert (ship["match_status"], ship["cost_center_id"]) == ("project", None)
    assert any("no cost center" in b for b in inv["finalize_blockers"])


async def test_linking_a_project_shipment_by_hand_prefills_too(admin_client, world, rd):  # noqa: F811
    s = (await _register_project(admin_client, world, tracking="1Z999AA10123456784")).json()
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    ship = inv["lines"][0]
    assert ship["match_status"] == "unregistered"
    r = await admin_client.patch(f"/api/v1/freight/invoices/{inv['id']}/lines/{ship['id']}",
                                 json={"shipment_id": s["id"]})
    line = r.json()["lines"][0]
    assert (line["match_status"], line["cost_center_id"], line["shipment_project_info"]) == \
        ("project", str(rd), "Dryer 3 retrofit — PRJ-2026-014")


async def test_settings_need_freight_admin(test_engine, world, rd):  # noqa: F811
    from tests.conftest import _authenticated_client
    async with await _authenticated_client(test_engine, "warehouse_staff") as c:
        r = await c.put("/api/v1/freight/settings", json={"project_cost_center_id": None})
        assert r.status_code == 403
        assert (await c.get("/api/v1/freight/settings")).status_code == 403
