"""Courier freight payment (Freight): shipments, carrier-invoice matching, Freight PA.

Design: docs/superpowers/specs/2026-10-19-freight-payment-design.md. The UPS
fixture below is the real sample invoice 00004E4F77416 (2026-10-10): one
two-package shipment, two on-call pickups, a late-payment fee, and HST only on
the pickups.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import freight as crud
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.freight import FreightShipment
from app.models.vendor import Vendor

_KEYS = ("epms.freight.register", "epms.freight.manage", "epms.freight.admin")


@pytest.fixture(autouse=True)
def _no_outbound_calls(monkeypatch):
    """finance_sync / budget-api are fail-open HTTP calls; keep tests offline."""
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.api.v1.freight.finance_sync.sync_ap_invoice", _noop)
    monkeypatch.setattr("app.api.v1.freight.budget_client.ensure_known_budget_code", _noop)
    monkeypatch.setattr("app.api.v1.freight.budget_client.get_balance", _noop)


async def _grant(test_engine, role: str, *keys: str):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        for k in keys:
            await db.execute(text(
                "INSERT INTO permission_defs(key,module,label,sort) VALUES (:k,'epms',:k,900) "
                "ON CONFLICT (key) DO NOTHING"), {"k": k})
            await db.execute(text(
                "INSERT INTO role_permissions(role_code,permission_key) VALUES (:r,:k) "
                "ON CONFLICT DO NOTHING"), {"r": role, "k": k})
        await db.commit()


@pytest.fixture
async def world(test_engine, admin_client):
    """Two departments (one with two cost centres), the UPS vendor and carrier."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    tag = uuid.uuid4().hex[:6].upper()
    async with factory() as db:
        sc = Department(code=f"SC{tag}", name=f"Supply Chain {tag}")
        qa = Department(code=f"QA{tag}", name=f"Quality {tag}")
        db.add_all([sc, qa])
        await db.flush()
        sc_sell = CostCenter(code=f"SELL-{tag}", name="SC Selling", department_id=sc.id)
        sc_ga = CostCenter(code=f"GA-{tag}", name="SC G&A", department_id=sc.id)
        qa_lab = CostCenter(code=f"LAB-{tag}", name="QA Lab", department_id=qa.id)
        ups = Vendor(code=f"V-UPS-{tag}", name=f"UPS Canada Ltd. {tag}", category="Logistics",
                     contact_name="Billing", contact_email="billing@ups.example")
        db.add_all([sc_sell, sc_ga, qa_lab, ups])
        await db.commit()
        ids = {"sc": sc.id, "qa": qa.id, "sc_sell": sc_sell.id, "sc_ga": sc_ga.id,
               "qa_lab": qa_lab.id, "ups_vendor": ups.id}
    r = await admin_client.post("/api/v1/freight/carriers", json={
        "vendor_id": str(ids["ups_vendor"]), "code": f"U{tag}", "name": "UPS",
        "tracking_pattern": r"1Z[0-9A-Z]{16}", "default_cost_center_id": str(ids["sc_ga"])})
    assert r.status_code == 201, r.text
    ids["carrier"] = r.json()["id"]
    return ids


def _ups_invoice(w, number="00004E4F77416", amount="85.14", lines=None):
    return {
        "carrier_id": w["carrier"], "vendor_invoice_number": number,
        "invoice_date": "2026-10-10", "due_date": "2026-10-26", "currency": "CAD",
        "amount": amount, "tax_amount": "1.68",
        "lines": lines if lines is not None else [
            {"charge_type": "shipment", "tracking_no": "1Z4E4F776832551028",
             "package_tracking_nos": ["1Z4E4F776832551028", "1Z4E4F776821544637"],
             "reference": "44461957964814303-93747573", "ship_date": "2026-10-06",
             "description": "Worldwide Service — Standard", "amount": "51.28", "tax_amount": "0"},
            {"charge_type": "pickup", "reference": "29B0D7A1695", "ship_date": "2026-10-06",
             "description": "On-Call Pickup", "amount": "6.48", "tax_amount": "0.84"},
            {"charge_type": "pickup", "reference": "29070E50AA1", "ship_date": "2026-10-08",
             "description": "On-Call Pickup", "amount": "6.48", "tax_amount": "0.84"},
            {"charge_type": "fee", "ship_date": "2026-09-12", "description": "Late Payment Fee",
             "amount": "20.90", "tax_amount": "0"},
        ],
    }


async def _register(client, w, tracking, cc_key="qa_lab"):
    return await client.post("/api/v1/freight/shipments", json={
        "carrier_id": w["carrier"], "tracking_no": tracking, "cost_center_id": str(w[cc_key])})


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_normalize_tracking():
    assert crud.normalize_tracking(" 1z 4e4f-7768.3255 1028 ") == "1Z4E4F776832551028"
    assert crud.normalize_tracking("   ") is None
    assert crud.normalize_tracking(None) is None


def test_spread_keeps_every_cent():
    parts = crud._spread(Decimal("1.68"), [Decimal("6.48"), Decimal("6.48"), Decimal("51.28")])
    assert sum(parts) == Decimal("1.68")
    assert crud._spread(Decimal("1.00"), [Decimal("1"), Decimal("1"), Decimal("1")]) == \
        [Decimal("0.34"), Decimal("0.33"), Decimal("0.33")]
    assert crud._spread(Decimal("0.10"), [Decimal(0), Decimal(0)]) == [Decimal("0.05"), Decimal("0.05")]


# ── Default cost centre (D11) ─────────────────────────────────────────────────

async def test_default_cost_center_rules(test_engine, world):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def budget_on(cc_id):
        async def fn(c, year):
            return {"annual_budget": 450900} if c == cc_id else {"annual_budget": 0}
        return fn

    async with factory() as db:
        # Several cost centres, one budgeted for freight → that one.
        d = await crud.default_cost_center(db, world["sc"], await budget_on(world["sc_sell"]))
        assert (d["cost_center_id"], d["reason"]) == (world["sc_sell"], "freight_budget")
        # Several, none budgeted → no default; the user picks.
        d = await crud.default_cost_center(db, world["sc"], await budget_on(None))
        assert (d["cost_center_id"], d["reason"]) == (None, "none")
        # budget-api unreachable (None) behaves like "not budgeted".
        async def down(c, y):
            return None
        d = await crud.default_cost_center(db, world["sc"], down)
        assert d["cost_center_id"] is None
        # A single cost centre is the default regardless of budget.
        d = await crud.default_cost_center(db, world["qa"], down)
        assert (d["cost_center_id"], d["reason"]) == (world["qa_lab"], "single")


# ── Shipments ─────────────────────────────────────────────────────────────────

async def test_register_normalises_validates_and_refuses_duplicates(admin_client, world):
    r = await _register(admin_client, world, " 1z4e4f77 6821544637 ")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["tracking_no"] == "1Z4E4F776821544637"
    assert body["status"] == "pending"
    assert body["department_id"] == str(world["qa"])
    assert body["cost_center_name"] == "QA Lab"

    dup = await _register(admin_client, world, "1Z4E4F776821544637", "sc_sell")
    assert dup.status_code == 409 and "already registered" in dup.json()["detail"]

    bad = await _register(admin_client, world, "877326061702")  # a FedEx number on UPS
    assert bad.status_code == 422 and "does not look like" in bad.json()["detail"]


async def test_registrar_sees_only_own_shipments(test_engine, admin_client, world):
    from tests.conftest import _authenticated_client
    await _grant(test_engine, "procurement_officer", "epms.freight.register")
    await _register(admin_client, world, "1Z4E4F770000000001")
    async with await _authenticated_client(test_engine, "procurement_officer") as buyer:
        r = await _register(buyer, world, "1Z4E4F770000000002")
        assert r.status_code == 201, r.text
        mine = (await buyer.get("/api/v1/freight/shipments")).json()
        assert [s["tracking_no"] for s in mine["items"]] == ["1Z4E4F770000000002"]
        # Registrars cannot see invoices or raise PAs.
        assert (await buyer.get("/api/v1/freight/invoices")).status_code == 403
        assert (await buyer.post("/api/v1/freight/pa", json={"invoice_ids": [str(uuid.uuid4())]})
                ).status_code == 403
    # Admission control: the manager sees both.
    allr = (await admin_client.get("/api/v1/freight/shipments?search=1Z4E4F77000000000")).json()
    assert {s["tracking_no"] for s in allr["items"]} == {"1Z4E4F770000000001", "1Z4E4F770000000002"}


async def test_no_freight_permission_is_refused(test_engine, world):
    from tests.conftest import _authenticated_client
    async with await _authenticated_client(test_engine, "warehouse_staff") as c:
        assert (await c.get("/api/v1/freight/shipments")).status_code == 403
        assert (await c.get("/api/v1/freight/me")).json() == \
            {"register": False, "manage": False, "admin": False}


# ── Invoices and matching ─────────────────────────────────────────────────────

async def test_ups_sample_auto_matches(admin_client, world):
    # The buyer registered the CHILD package number; the invoice leads with the parent.
    await _register(admin_client, world, "1Z4E4F776821544637", "qa_lab")
    r = await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))
    assert r.status_code == 201, r.text
    inv = r.json()
    ship, pick1, pick2, fee = inv["lines"]
    assert (ship["match_status"], ship["cost_center_id"]) == ("auto", str(world["qa_lab"]))
    # Pickup on the shipment's date follows the shipment …
    assert (pick1["linked_line_id"], pick1["cost_center_id"]) == (ship["id"], str(world["qa_lab"]))
    # … one with no shipment that day, and the late fee, go to the carrier default.
    assert (pick2["match_status"], pick2["cost_center_id"]) == ("default", str(world["sc_ga"]))
    assert (fee["match_status"], fee["cost_center_id"]) == ("default", str(world["sc_ga"]))
    assert inv["finalize_blockers"] == []
    assert Decimal(inv["lines_amount_total"]) == Decimal("85.14")
    assert Decimal(inv["lines_tax_total"]) == Decimal("1.68")

    fin = await admin_client.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")
    assert fin.status_code == 200, fin.text
    assert fin.json()["status"] == "matched"
    s = (await admin_client.get("/api/v1/freight/shipments?search=1Z4E4F776821544637")).json()["items"][0]
    assert s["status"] == "billed"
    assert s["ship_date"] == "2026-10-06"
    assert s["invoice_number"] == "00004E4F77416"


async def test_duplicate_invoice_is_refused(admin_client, world):
    assert (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).status_code == 201
    again = await admin_client.post("/api/v1/freight/invoices",
                                    json=_ups_invoice(world, number=" 00004e4f77416 "))
    assert again.status_code == 409 and "already recorded" in again.json()["detail"]
    chk = await admin_client.get("/api/v1/freight/invoices/duplicate-check", params={
        "carrier_id": world["carrier"], "vendor_invoice_number": "00004E4F77416"})
    assert chk.json()["duplicate"] is True
    chk2 = await admin_client.get("/api/v1/freight/invoices/duplicate-check", params={
        "carrier_id": world["carrier"], "vendor_invoice_number": "NEVER-SEEN"})
    assert chk2.json()["duplicate"] is False


async def test_finalize_refuses_unbalanced_lines(admin_client, world):
    payload = _ups_invoice(world, amount="90.00")   # lines add to 85.14
    inv = (await admin_client.post("/api/v1/freight/invoices", json=payload)).json()
    assert any("Lines add up to 85.14" in b for b in inv["finalize_blockers"])
    r = await admin_client.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")
    assert r.status_code == 422


async def test_residual_tax_is_spread_over_lines(admin_client, world):
    lines = _ups_invoice(world)["lines"]
    for ln in lines:
        ln["tax_amount"] = "0"
    inv = (await admin_client.post("/api/v1/freight/invoices",
                                   json=_ups_invoice(world, lines=lines))).json()
    assert Decimal(inv["lines_tax_total"]) == Decimal("1.68")
    assert not any("Line taxes" in b for b in inv["finalize_blockers"])
    # Positive lines only, in proportion: the 20.90 fee carries the largest share after the shipment.
    taxes = [Decimal(l["tax_amount"]) for l in inv["lines"]]
    assert taxes[0] > taxes[3] > taxes[1] == taxes[2]


async def test_unregistered_shipment_is_recorded_on_finalize(admin_client, world):
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    ship = inv["lines"][0]
    assert (ship["match_status"], ship["cost_center_id"]) == ("unregistered", None)
    assert any("no cost center" in b for b in inv["finalize_blockers"])
    # The pickup that follows it has nothing to follow yet either.
    r = await admin_client.patch(f"/api/v1/freight/invoices/{inv['id']}/lines/{ship['id']}",
                                 json={"cost_center_id": str(world["sc_sell"])})
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["lines"][0]["match_status"] == "manual"
    fin = await admin_client.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")
    assert fin.status_code == 200, fin.text
    s = (await admin_client.get("/api/v1/freight/shipments?search=1Z4E4F776832551028")).json()["items"][0]
    assert (s["source"], s["status"], s["cost_center_id"]) == ("from_invoice", "billed", str(world["sc_sell"]))


async def test_second_billing_of_a_shipment_needs_a_decision(admin_client, world):
    await _register(admin_client, world, "1Z4E4F776832551028", "qa_lab")
    first = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    assert (await admin_client.post(f"/api/v1/freight/invoices/{first['id']}/finalize")).status_code == 200

    second = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(
        world, number="00004E4F77426", amount="5.00", lines=[
            {"charge_type": "shipment", "tracking_no": "1Z4E4F776832551028", "ship_date": "2026-10-06",
             "description": "Address correction", "amount": "5.00", "tax_amount": "1.68"}]))).json()
    line = second["lines"][0]
    assert line["match_status"] == "duplicate_billing"
    assert line["previously_billed_on"].startswith("00004E4F77416")
    assert (await admin_client.post(f"/api/v1/freight/invoices/{second['id']}/finalize")).status_code == 422
    # Confirming the cost centre is the decision.
    ok = await admin_client.patch(f"/api/v1/freight/invoices/{second['id']}/lines/{line['id']}",
                                  json={"cost_center_id": line["cost_center_id"]})
    assert ok.json()["finalize_blockers"] == []
    # An adjustment line on the same number would have followed it without asking.
    assert (await admin_client.post(f"/api/v1/freight/invoices/{second['id']}/finalize")).status_code == 200


async def test_generic_invoice_endpoints_refuse_freight_invoices(admin_client, world):
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match", json={"po_id": str(uuid.uuid4())})
    assert r.status_code == 409 and "its invoice page" in r.json()["detail"]
    assert (await admin_client.delete(f"/api/v1/invoices/{inv['id']}")).status_code == 409
    # Read stays open: the invoice is still an EPMS invoice.
    assert (await admin_client.get(f"/api/v1/invoices/{inv['id']}")).status_code == 200


# ── Freight PA ────────────────────────────────────────────────────────────────

async def _matched_invoice(admin_client, world):
    await _register(admin_client, world, "1Z4E4F776821544637", "qa_lab")
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    assert (await admin_client.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")).status_code == 200
    return inv


async def test_freight_pa_snapshot_and_visibility(admin_client, world):
    inv = await _matched_invoice(admin_client, world)
    r = await admin_client.post("/api/v1/freight/pa", json={"invoice_ids": [inv["id"]]})
    assert r.status_code == 201, r.text
    pa_id = r.json()["id"]

    pa = (await admin_client.get(f"/api/v1/pa/{pa_id}")).json()
    assert pa["source"] == "freight" and pa["po_id"] is None and pa["agreement_id"] is None
    assert Decimal(pa["subtotal"]) == Decimal("85.14")
    assert Decimal(pa["tax_amount"]) == Decimal("1.68")
    assert Decimal(pa["payment_amount"]) == Decimal("86.82")   # the invoice's amount due
    alloc = {a["cost_center_id"]: a for a in pa["cost_allocations"]}
    # Shipment 51.28 + its pickup 6.48 (+0.84 tax) → QA Lab; other pickup + late fee → SC G&A.
    assert Decimal(alloc[str(world["qa_lab"])]["amount"]) == Decimal("57.76")
    assert Decimal(alloc[str(world["qa_lab"])]["tax_amount"]) == Decimal("0.84")
    assert Decimal(alloc[str(world["sc_ga"])]["amount"]) == Decimal("27.38")
    assert {a["budget_code"] for a in pa["cost_allocations"]} == {"CRM0150102"}

    listed = (await admin_client.get("/api/v1/pa", params={"search": pa["pa_number"]})).json()
    assert [p["id"] for p in listed["items"]] == [pa_id]
    by_dept = (await admin_client.get("/api/v1/pa", params={"department_id": str(world["qa"])})).json()
    assert pa_id in [p["id"] for p in by_dept["items"]]

    # One invoice, one PA; and a held invoice cannot be reopened.
    again = await admin_client.post("/api/v1/freight/pa", json={"invoice_ids": [inv["id"]]})
    assert again.status_code == 409
    assert (await admin_client.post(f"/api/v1/freight/invoices/{inv['id']}/reopen")).status_code == 409


async def test_freight_pa_amounts_are_not_editable(admin_client, world):
    inv = await _matched_invoice(admin_client, world)
    pa_id = (await admin_client.post("/api/v1/freight/pa", json={"invoice_ids": [inv["id"]]})).json()["id"]
    bad = await admin_client.patch(f"/api/v1/pa/{pa_id}", json={"subtotal": "1.00"})
    assert bad.status_code == 422
    ok = await admin_client.patch(f"/api/v1/pa/{pa_id}", json={"title": "UPS week of Oct 10"})
    assert ok.status_code == 200 and ok.json()["title"] == "UPS week of Oct 10"


async def test_unfinalised_invoice_cannot_be_paid(admin_client, world):
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    r = await admin_client.post("/api/v1/freight/pa", json={"invoice_ids": [inv["id"]]})
    assert r.status_code == 422 and "Finish matching" in r.json()["detail"]


async def test_restricted_chain_approver_can_open_freight_pa(test_engine, admin_client, world):
    """GM and OPM are restricted roles and their approve_pa tasks are broadcast,
    so the task shortcut never admits them; the chain-role rule must."""
    from tests.conftest import _authenticated_client
    inv = await _matched_invoice(admin_client, world)
    pa_id = (await admin_client.post("/api/v1/freight/pa", json={"invoice_ids": [inv["id"]]})).json()["id"]
    async with await _authenticated_client(test_engine, "gm") as gm:
        assert (await gm.get(f"/api/v1/pa/{pa_id}")).status_code == 200
        assert pa_id in [p["id"] for p in (await gm.get("/api/v1/pa")).json()["items"]]
    # Denial control: a requester who neither raised it nor approves it.
    async with await _authenticated_client(test_engine, "requester") as req:
        assert (await req.get(f"/api/v1/pa/{pa_id}")).status_code == 404
        assert pa_id not in [p["id"] for p in (await req.get("/api/v1/pa")).json()["items"]]


async def test_direct_pa_still_hidden(test_engine, admin_client, world):
    """OA's retired Direct PA (no PO, no agreement, source oa_direct since
    dp01_direct_pa — 'direct' is now EPMS's own Direct PA) stays out of EPMS."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    from app.models.pa import PaymentApplication
    async with factory() as db:
        uid = (await db.execute(text("SELECT id FROM users LIMIT 1"))).scalar_one()
        pa = PaymentApplication(pa_number=f"PA-DIR-x{uuid.uuid4().hex[:4]}", title="Direct",
                                vendor_id=world["ups_vendor"], vendor_name="UPS", invoice_ids=[],
                                gr_ids=[], subtotal=Decimal("1"), payment_amount=Decimal("1"),
                                created_by=uid, source="oa_direct")
        db.add(pa)
        await db.commit()
        pid = pa.id
    assert (await admin_client.get(f"/api/v1/pa/{pid}")).status_code == 404


async def test_freight_manager_without_pa_write_can_edit_title(test_engine, admin_client, world):
    from tests.conftest import _authenticated_client
    inv = await _matched_invoice(admin_client, world)
    pa_id = (await admin_client.post("/api/v1/freight/pa", json={"invoice_ids": [inv["id"]]})).json()["id"]
    await _grant(test_engine, "warehouse_staff", "epms.freight.manage")
    async with await _authenticated_client(test_engine, "warehouse_staff") as c:
        r = await c.patch(f"/api/v1/pa/{pa_id}", json={"title": "Edited by freight manager"})
        assert r.status_code == 200, r.text
    # Denial control: same role without the freight key, on the same PA.
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(text("DELETE FROM role_permissions WHERE role_code='warehouse_staff' "
                              "AND permission_key='epms.freight.manage'"))
        await db.commit()
    async with await _authenticated_client(test_engine, "warehouse_staff") as c:
        assert (await c.patch(f"/api/v1/pa/{pa_id}", json={"title": "x"})).status_code == 403


# ── Courier invoices on the Invoices page ─────────────────────────────────────

async def test_restricted_freight_manager_sees_courier_invoices(test_engine, admin_client, world):
    """Courier invoices hang off no PO, so a PO-restricted caller reaches them only
    through epms.freight.manage — on the list, the detail page and its chain."""
    from tests.conftest import _authenticated_client
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()

    async def _sees(c) -> tuple[bool, int, int]:
        listed = inv["id"] in [i["id"] for i in (await c.get(
            "/api/v1/invoices", params={"route": "freight", "page_size": 200})).json()["items"]]
        return (listed, (await c.get(f"/api/v1/invoices/{inv['id']}")).status_code,
                (await c.get(f"/api/v1/invoices/{inv['id']}/chain")).status_code)

    # Denial control: a restricted requester without the key sees none of it.
    async with await _authenticated_client(test_engine, "requester") as c:
        assert await _sees(c) == (False, 404, 404)
    await _grant(test_engine, "requester", "epms.freight.manage")
    try:
        async with await _authenticated_client(test_engine, "requester") as c:
            assert await _sees(c) == (True, 200, 200)
            # The widening is courier invoices only, not every invoice.
            others = (await c.get("/api/v1/invoices", params={"page_size": 200})).json()["items"]
            assert {i["match_route"] for i in others} <= {"freight"}
    finally:
        # role_permissions outlives the test; leave requester as the matrix had it.
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            await db.execute(text("DELETE FROM role_permissions WHERE role_code='requester' "
                                  "AND permission_key='epms.freight.manage'"))
            await db.commit()


async def test_invoice_list_route_filter(test_engine, admin_client, world):
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    ids = lambda r: {i["id"] for i in r.json()["items"]}  # noqa: E731
    assert inv["id"] in ids(await admin_client.get("/api/v1/invoices", params={"route": "freight"}))
    assert inv["id"] not in ids(await admin_client.get("/api/v1/invoices", params={"route": "po"}))
    assert inv["id"] not in ids(await admin_client.get("/api/v1/invoices", params={"route": "agreement"}))
    assert (await admin_client.get("/api/v1/invoices", params={"route": "bogus"})).status_code == 422



async def test_invoice_reader_gets_the_courier_panel_read_only(test_engine, admin_client, world):
    """The Invoices page shows a courier invoice's matching panel to everyone who
    may read the invoice; only epms.freight.manage may change it."""
    from tests.conftest import _authenticated_client
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world))).json()
    line = inv["lines"][0]
    async with await _authenticated_client(test_engine, "finance_manager") as fm:
        assert (await fm.get("/api/v1/freight/me")).json()["manage"] is False
        r = await fm.get(f"/api/v1/freight/invoices/{inv['id']}")
        assert r.status_code == 200 and len(r.json()["lines"]) == len(inv["lines"])
        assert (await fm.patch(f"/api/v1/freight/invoices/{inv['id']}/lines/{line['id']}",
                               json={"cost_center_id": str(world["sc_sell"])})).status_code == 403
        assert (await fm.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")).status_code == 403
    # Denial control: a restricted caller who cannot read the invoice gets nothing.
    async with await _authenticated_client(test_engine, "requester") as c:
        assert (await c.get(f"/api/v1/freight/invoices/{inv['id']}")).status_code == 404


async def test_assistant_reads_a_courier_invoice_as_freight(test_engine, admin_client, world):
    """billed_against (ISS-0013) predates Freight: a matched courier invoice has
    neither a PO nor an agreement and must not read as "not matched yet"; nor is
    it ever "matched but not received"."""
    from app.models.invoice import Invoice
    inv = await _matched_invoice(admin_client, world)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(select(Invoice.billed_against, Invoice.po_receipt_recorded)
                                .where(Invoice.id == uuid.UUID(inv["id"])))).one()
        assert tuple(row) == ("freight", None)
        assert (await db.get(Invoice, uuid.UUID(inv["id"]))).billed_against == "freight"


# ── One upload page: courier invoices are uploaded like any invoice ───────────

async def test_invoice_uploader_creates_a_courier_invoice_but_does_not_match_it(test_engine, admin_client, world):
    """The upload form recognises a carrier's invoice by its vendor and saves it
    through the Freight API, so creating one takes the ordinary invoice-upload
    permission. Matching stays on epms.freight.manage."""
    from tests.conftest import _authenticated_client
    async with await _authenticated_client(test_engine, "ap_clerk") as ap:
        assert (await ap.get("/api/v1/freight/me")).json()["manage"] is False
        carriers = (await ap.get("/api/v1/freight/carriers")).json()
        assert world["carrier"] in [c["id"] for c in carriers]
        assert (await ap.get("/api/v1/freight/invoices/duplicate-check", params={
            "carrier_id": world["carrier"], "vendor_invoice_number": "AP-1"})).status_code == 200
        r = await ap.post("/api/v1/freight/invoices", json=_ups_invoice(world, number="AP-1"))
        assert r.status_code == 201, r.text
        inv = r.json()
        assert (await ap.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")).status_code == 403
    # Denial control: no upload permission and no freight key.
    async with await _authenticated_client(test_engine, "warehouse_staff") as c:
        assert (await c.get("/api/v1/freight/carriers")).status_code == 403
        assert (await c.post("/api/v1/freight/invoices",
                             json=_ups_invoice(world, number="WH-1"))).status_code == 403


async def test_a_carriers_invoice_cannot_go_in_as_a_po_invoice(admin_client, world):
    r = await admin_client.post("/api/v1/invoices", json={
        "vendor_id": str(world["ups_vendor"]), "vendor_invoice_number": "PO-ROUTE-1",
        "invoice_date": "2026-10-10", "due_date": "2026-10-26", "currency": "CAD",
        "amount": "10.00", "tax_amount": "0"})
    assert r.status_code == 409 and "is a courier" in r.json()["detail"]


async def test_ap_assigns_a_courier_invoice_and_the_assignee_matches_it(test_engine, admin_client, world):
    """Like any invoice, AP assigns the matching to someone; the open
    match_invoice task is what lets them match it — no Freight key needed.
    Finalising closes the task; the Freight PA stays with the freight manager."""
    from tests.conftest import _authenticated_client
    from app.models.task import Task
    await _register(admin_client, world, "1Z4E4F776821544637", "qa_lab")
    inv = (await admin_client.post("/api/v1/freight/invoices", json=_ups_invoice(world, number="ASG-1"))).json()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def open_tasks():
        async with factory() as db:
            return (await db.execute(select(Task).where(
                Task.document_id == uuid.UUID(inv["id"]), Task.type == "match_invoice",
                Task.is_completed.is_(False)))).scalars().all()

    async with await _authenticated_client(test_engine, "warehouse_staff") as other, \
               await _authenticated_client(test_engine, "warehouse_staff") as who:
        from jose import jwt
        me = jwt.get_unverified_claims(who.headers["Authorization"].split()[1])["sub"]
        # Before the assignment: no access to the matching.
        assert (await who.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")).status_code == 403

        r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/assign-match", json={"user_id": me})
        assert r.status_code == 200, r.text
        tasks = await open_tasks()
        assert [t.title for t in tasks] == [f"Match courier invoice {inv['internal_ref']} to shipments"]

        # The task links to the invoice page; it must open for the assignee.
        assert (await who.get(f"/api/v1/invoices/{inv['id']}")).status_code == 200
        assert (await other.get(f"/api/v1/invoices/{inv['id']}")).status_code == 404
        d = await who.get(f"/api/v1/freight/invoices/{inv['id']}")
        assert d.status_code == 200 and d.json()["can_match"] is True
        assert d.json()["match_assignee_id"] == me
        # Denial control: same role, not assigned.
        assert (await other.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")).status_code == 403

        fin = await who.post(f"/api/v1/freight/invoices/{inv['id']}/finalize")
        assert fin.status_code == 200, fin.text
        assert fin.json()["status"] == "matched"
        assert await open_tasks() == []
        # Paying it is still the freight manager's.
        assert (await who.post("/api/v1/freight/pa", json={"invoice_ids": [inv["id"]]})).status_code == 403
