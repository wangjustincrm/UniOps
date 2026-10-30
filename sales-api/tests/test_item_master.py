"""Item Master over HTTP (P3): items with their units (FR-OB-02, AC-OB-14),
partner buyer parts and DC routing — CRUD, CSV with composite keys, the
Excel / barcode guards, permissions."""
import csv
import io

import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.deps import get_uow_factory
from app.main import app
from app.models.records import AuditRecord
from app.platform.audit import AuditTrail
from app.platform.uow import UnitOfWorkFactory
from app.schemas.common import BARCODE_MESSAGE, EXCEL_MESSAGE
from tests.conftest import make_token

GRANTS = {"sales_operator": {"sales.read", "sales.config"}, "sales_reader": {"sales.read"}}


async def matrix(session, actor, key):  # noqa: ARG001
    return key in GRANTS.get(actor.role or "", set())


OP = {"Authorization": f"Bearer {make_token('sales_operator')}"}
READER = {"Authorization": f"Bearer {make_token('sales_reader')}"}
ITEMS, PC, DC = "/api/v1/config/items", "/api/v1/config/partner-codes", "/api/v1/config/dc-routing"


@pytest_asyncio.fixture
async def http(db_engine):
    async with db_engine.begin() as c:
        await c.execute(sa.text("TRUNCATE sales_order_lines, sales_orders, sales_item_partner_codes, sales_dc_routing, "
                                "sales_item_units, sales_items, sales_connections, sales_partners, sales_audit "
                                "RESTART IDENTITY CASCADE"))
    factory = UnitOfWorkFactory(async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False),
                                permissions=matrix, audit=AuditTrail(AuditRecord))
    app.dependency_overrides[get_uow_factory] = lambda: factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for code in ("WMT", "LBL"):
            r = await c.post("/api/v1/config/partners", headers=OP, json={
                "code": code, "name": code, "channel": "TRUECOMMERCE", "settlement_mode": "INVOICE",
                "sending_mode": "AUTOMATIC", "delay_minutes": 0, "invoice_method": "810_JSON",
                "dc_routing_required": code == "WMT"})
            assert r.status_code == 201, r.text
        yield c
    app.dependency_overrides.clear()


def item(code="CF0092", **over):
    return {"item_code": code, "description": "Infant formula 900 g", "sku": f"SKU-{code}", "upc": "062639300012",
            "gtin": "10062639300019", "gross_weight_lb_per_ea": "2.2", "warehouse_held": True,
            "units": [{"unit": "EA", "factor_ea": 1}, {"unit": "case", "factor_ea": 6}], **over}


def errors(r):
    return {e["field"]: e["message"] for e in r.json()["error"]["details"].get("errors", [])} or \
        {r.json()["error"]["details"].get("field"): r.json()["error"]["code"]}


async def test_an_item_carries_its_units_and_ea_is_exactly_one(http):
    r = await http.post(ITEMS, json=item(), headers=OP)
    assert r.status_code == 201, r.text
    it = r.json()
    assert [(u["unit"], u["factor_ea"]) for u in it["units"]] == [("EA", "1.0000"), ("CASE", "6.0000")]
    # AC-OB-14: a conversion set without EA = 1 is refused
    for units in ([{"unit": "CASE", "factor_ea": 6}], [{"unit": "EA", "factor_ea": 2}]):
        r = await http.post(ITEMS, json=item("CF0093", units=units), headers=OP)
        assert r.status_code == 422 and r.json()["error"]["code"] == "item.ea_required"
    r = await http.post(ITEMS, json=item("CF0094", units="EA=1; CASE=6; CASE=12"), headers=OP)
    assert r.json()["error"]["code"] == "item.unit_twice"
    assert (await http.post(ITEMS, json=item("CF0095"), headers=READER)).status_code == 403


async def test_units_are_replaced_as_a_set_and_never_deleted(http, db_engine):
    it = (await http.post(ITEMS, json=item(units="EA=1; CASE=6; 2PACK=2"), headers=OP)).json()
    r = await http.put(f"{ITEMS}/{it['id']}?version=1", json={"units": "EA=1; CASE=12"}, headers=OP)
    assert r.status_code == 200, r.text
    assert [(u["unit"], u["factor_ea"]) for u in r.json()["units"]] == [("EA", "1.0000"), ("CASE", "12.0000")]
    async with db_engine.connect() as c:
        rows = (await c.execute(sa.text("SELECT unit, factor_ea, active FROM sales_item_units ORDER BY id"))).all()
    assert [(u, str(f), a) for u, f, a in rows] == [("EA", "1.0000", True), ("CASE", "12.0000", True),
                                                    ("2PACK", "2.0000", False)]  # deactivated, kept


async def test_codes_and_barcodes_refuse_what_excel_does_to_them(http):
    r = await http.post(ITEMS, json=item("1.22E+11"), headers=OP)
    assert errors(r) == {"item_code": EXCEL_MESSAGE}
    r = await http.post(ITEMS, json=item(upc="62639300012", gtin="1.00626E+13"), headers=OP)  # leading zero lost
    assert errors(r) == {"upc": BARCODE_MESSAGE, "gtin": BARCODE_MESSAGE}
    assert (await http.post(ITEMS, json=item(upc="06263930", gtin=None), headers=OP)).status_code == 201


def _csv(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue().encode()


async def _import(http, path, rows, dry_run=False):
    r = await http.post(f"{path}/import.csv?dry_run={str(dry_run).lower()}",
                        files={"file": ("x.csv", _csv(rows), "text/csv")}, headers=OP)
    assert r.status_code == 200, r.text
    return r.json()


async def test_items_csv_round_trip_with_units(http):
    await http.post(ITEMS, json=item(), headers=OP)
    exported = (await http.get(f"{ITEMS}/export.csv", headers=READER)).text
    rows = list(csv.DictReader(io.StringIO(exported)))
    assert rows[0]["units"] == "EA=1; CASE=6" and rows[0]["version"] == "1"
    rep = await _import(http, ITEMS, rows, dry_run=True)
    assert rep["ok"] and rep["counts"] == {"create": 0, "update": 0, "unchanged": 1}
    rows[0]["units"] = "EA=1; CASE=6; 2PACK=2"
    rows.append({**rows[0], "item_code": "CF0100", "version": "", "units": "EA=1"})
    rep = await _import(http, ITEMS, rows)
    assert rep["ok"] and rep["written"] and rep["counts"] == {"create": 1, "update": 1, "unchanged": 0}
    got = {i["item_code"]: [u["unit"] for u in i["units"]] for i in (await http.get(ITEMS, headers=OP)).json()["items"]}
    assert got == {"CF0092": ["EA", "CASE", "2PACK"], "CF0100": ["EA"]}
    rep = await _import(http, ITEMS, [{**rows[1], "item_code": "CF0101", "units": "CASE=6"}])
    assert not rep["ok"] and rep["errors"][0]["code"] == "item.ea_required"


async def test_units_carry_shopify_titles_in_the_api_and_the_csv(http):
    r = await http.post(ITEMS, json=item(units="EA=1 [Single]; CASE=6 [Case (6 Cans), Case of 6]; 2PACK=2 [2-Can]"),
                        headers=OP)
    assert r.status_code == 201, r.text
    assert [(u["unit"], u["shopify_titles"]) for u in r.json()["units"]] == [
        ("EA", ["Single"]), ("CASE", ["Case (6 Cans)", "Case of 6"]), ("2PACK", ["2-Can"])]
    rows = list(csv.DictReader(io.StringIO((await http.get(f"{ITEMS}/export.csv", headers=READER)).text)))
    assert rows[0]["units"] == "EA=1 [Single]; CASE=6 [Case (6 Cans), Case of 6]; 2PACK=2 [2-Can]"
    assert (await _import(http, ITEMS, rows, dry_run=True))["counts"]["unchanged"] == 1
    rows[0]["units"] = "EA=1; CASE=6 [Case (6 Cans)]; 2PACK=2 [2-Can, 2-Pack]"  # only the titles change
    rep = await _import(http, ITEMS, rows)
    assert rep["ok"] and rep["counts"] == {"create": 0, "update": 1, "unchanged": 0}
    [it] = (await http.get(ITEMS, headers=OP)).json()["items"]
    assert [(u["unit"], u["shopify_titles"]) for u in it["units"]] == [
        ("EA", []), ("CASE", ["Case (6 Cans)"]), ("2PACK", ["2-Can", "2-Pack"])]
    # one title for two units, a title with no letters or digits, an unclosed bracket
    for units, code in (("EA=1; CASE=6 [2-Pack]; 2PACK=2", "item.title_twice"),
                        ("EA=1; CASE=6 [Twin]; 2PACK=2 [twin]", "item.title_twice"),
                        ("EA=1; CASE=6 [--]", "item.title_empty")):
        r = await http.put(f"{ITEMS}/{it['id']}?version={it['version']}", json={"units": units}, headers=OP)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, (units, r.text)
    r = await http.put(f"{ITEMS}/{it['id']}?version={it['version']}", json={"units": "EA=1; CASE=6 [x"}, headers=OP)
    assert r.status_code == 422


async def test_buyer_parts_belong_to_one_partner_and_import_by_their_composite_key(http):
    await http.post(ITEMS, json=item(), headers=OP)
    await http.post(ITEMS, json=item("CF0093"), headers=OP)
    r = await http.post(PC, json={"partner_code": "WMT", "buyer_part": "555", "item_code": "CF0092"}, headers=OP)
    assert r.status_code == 201, r.text
    # the same buyer part under another partner is another mapping (AC-OB-12)
    assert (await http.post(PC, json={"partner_code": "LBL", "buyer_part": "555", "item_code": "CF0093"},
                            headers=OP)).status_code == 201
    r = await http.post(PC, json={"partner_code": "WMT", "buyer_part": "555", "item_code": "CF0093"}, headers=OP)
    assert r.status_code == 409 and r.json()["error"]["code"] == "partner_code.taken"
    for body, field in (({"partner_code": "NOPE", "buyer_part": "1", "item_code": "CF0092"}, "partner_code"),
                        ({"partner_code": "WMT", "buyer_part": "1", "item_code": "NOPE"}, "item_code")):
        r = await http.post(PC, json=body, headers=OP)
        assert r.status_code == 422 and r.json()["error"]["details"]["field"] == field
    rep = await _import(http, PC, [
        {"partner_code": "WMT", "buyer_part": "555", "item_code": "CF0093"},   # update by (partner, part)
        {"partner_code": "LBL", "buyer_part": "777", "item_code": "CF0092"},   # create
        {"partner_code": "lbl ", "buyer_part": "555", "item_code": "CF0093"},  # unchanged (key matched ignoring case)
    ])
    assert rep["ok"] and rep["counts"] == {"create": 1, "update": 1, "unchanged": 1}, rep
    rep = await _import(http, PC, [{"partner_code": "WMT", "buyer_part": "9", "item_code": "CF0092"},
                                   {"partner_code": "WMT", "buyer_part": "9", "item_code": "CF0093"}], dry_run=True)
    assert [e["code"] for e in rep["errors"]] == ["csv.duplicate_key"]


async def test_dc_routing_by_partner_and_gln(http):
    r = await http.post(DC, json={"partner_code": "WMT", "ship_to_gln": "0012345000011", "dc_number": "6080"}, headers=OP)
    assert r.status_code == 201, r.text
    rep = await _import(http, DC, [{"partner_code": "WMT", "ship_to_gln": "0012345000011", "dc_number": "6090",
                                    "notes": "", "active": "true"},
                                   {"partner_code": "WMT", "ship_to_gln": "0012345000028", "dc_number": "7036",
                                    "notes": "", "active": "true"}])
    assert rep["ok"] and rep["counts"] == {"create": 1, "update": 1, "unchanged": 0}
    r = await http.post(DC, json={"partner_code": "NOPE", "ship_to_gln": "1", "dc_number": "1"}, headers=OP)
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "partner_code"


async def test_the_list_meta_describes_the_fields_and_needs_read(http):
    nobody = {"Authorization": f"Bearer {make_token('requester')}"}
    assert (await http.get(f"{ITEMS}/meta", headers=nobody)).status_code == 403
    m = (await http.get(f"{ITEMS}/meta", headers=READER)).json()
    f = {x["name"]: x for x in m["fields"]}
    assert m["version"] == 1 and m["searchable"] and m["default_sort"] == ["item_code"]
    assert (f["upc"]["label"], f["upc"]["type"], f["upc"]["filterable"]) == ("UPC", "text", True)
    assert (f["warehouse_held"]["type"], f["gross_weight_lb_per_ea"]["type"]) == ("bool", "number")
    assert "units" not in f  # a list: nothing to filter, sort or show in a cell
    r = await http.get(f"{ITEMS}?filter=upc:is_null:&filter=description:contains:formula|milk", headers=READER)
    assert r.status_code == 200
    r = await http.get(f"{ITEMS}?filter=gross_weight_lb_per_ea:gt:heavy", headers=READER)
    assert r.status_code == 422 and r.json()["error"]["code"] == "query.bad_value"
