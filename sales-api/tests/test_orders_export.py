"""The Orders export (user 2026-11-24): everyone who sees Orders may export them, and the file says of a ship-to exactly
what the list shows — a Shopify order's place, another source's name and place — never a street, a contact, a bill-to
or a Shopify customer's name. Every export is in the audit log with the person and the query."""
import csv
import io
import json

import pytest
import sqlalchemy as sa

from app.platform.auditing import use_store
from app.platform.auditing.store import NullAuditingStore
from app.services.audit_logs import SqlAuditingStore
from app.services.order_views import OrderService
from tests.conftest import make_token
from tests.test_order_payment import FINANCE, _exec, _orders, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture

ADDRESS = {"name": "Zoé Privée", "first_name": "Zoé", "last_name": "Privée", "company": "Chez Zoé", "address1": "77 Secret Street", "address2": "Apt 9",
           "city": "Montréal", "province_code": "QC", "country_code": "CA", "zip": "H2X 1Y4", "phone": "514-555-0199", "email": "zoe@private.example"}
DC = {**ADDRESS, "name": "Costco DC 559", "city": "Brampton", "province_code": "ON", "first_name": None, "last_name": None, "company": None}
BILL = {**ADDRESS, "name": "Bill Tô", "address1": "88 Billing Road"}
NEVER = ("Secret Street", "Apt 9", "514-555-0199", "private.example", "H2X", "Billing Road", "Bill Tô", "Chez Zoé")
VISIBLE = "source_reference,partner_name,order_status,status_detail,shopify,ship_to_place,total_amount"


@pytest.fixture
async def two(api):  # noqa: F811
    o = await _orders(api, 9701, 9702)
    for n, a, kind in (("9701", ADDRESS, "SHOPIFY"), ("9702", DC, "EDI_850")):
        await _exec(api, f"UPDATE sales_orders SET ship_to = '{json.dumps(a)}'::jsonb, bill_to = '{json.dumps(BILL)}'::jsonb, "
                         f"source_type = '{kind}' WHERE id = {o[n]['id']}")
    return api


def rows(r):
    assert r.status_code == 200, r.text
    return list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))


@pytest.mark.parametrize("query", [f"?columns={VISIBLE}", ""])
async def test_neither_export_carries_more_than_the_list_shows(two, query):
    r = await two.get("/api/v1/orders/export.csv" + query, headers=FINANCE)
    text = r.content.decode("utf-8-sig")
    by = {x["Order ref"]: x for x in rows(r)}
    [shopify] = [x for k, x in by.items() if k.endswith("9701")]
    [dc] = [x for k, x in by.items() if k.endswith("9702")]
    assert shopify["Ship to"] == "Montréal, QC" and dc["Ship to"] == "Costco DC 559 — Brampton, ON"
    for s in (*NEVER, "Zoé", "Privée"):
        assert s not in text, s


async def test_all_fields_has_labels_and_the_three_added_and_visible_columns_have_the_page_s_own(two):
    [full, *_] = rows(await two.get("/api/v1/orders/export.csv", headers=FINANCE))
    assert {"Order ref", "Partner", "Payment", "Ship to", "Internal no.", "Received (Toronto)"} <= set(full)
    seen = rows(await two.get(f"/api/v1/orders/export.csv?columns={VISIBLE}", headers=FINANCE))
    assert list(seen[0]) == ["Order ref", "Partner", "Status", "Detail", "Shopify", "Ship to", "Total", "Currency"]
    assert seen[0]["Shopify"].startswith("Paid")


async def test_reading_is_enough_and_without_it_nothing_is_handed_out(two):
    nobody = {"Authorization": f"Bearer {make_token('nobody')}"}
    r = await two.get("/api/v1/orders/export.csv", headers=nobody)
    assert r.status_code == 403
    cap = (await two.get("/api/v1/orders/meta", headers=FINANCE)).json()["capabilities"]
    assert cap["export"]["allowed"] is True and cap["suggest"] is True
    assert [c["id"] for c in cap["export"]["computed"]] == ["status_detail", "shopify", "ship_to_place"]
    assert {a["id"]: a["allowed"] for a in cap["actions"]}["settle"] is True
    assert OrderService.resolved().export_permission is None


async def test_both_exports_are_in_the_audit_log_with_the_person_and_the_query(two):
    use_store(SqlAuditingStore(two.env.factory.session_factory))
    try:
        await two.get("/api/v1/orders/export.csv", headers=FINANCE)
        await two.get(f"/api/v1/orders/export.csv?columns={VISIBLE}&search=97&filter=source_type%3Aeq%3ASHOPIFY", headers=FINANCE)
        await two.get("/api/v1/orders?search=Zoe", headers=FINANCE)  # a list is not bulk data: not kept, nor its search
    finally:
        use_store(NullAuditingStore())
    logs = await two.env.q("SELECT l.*, (SELECT array_agg(service_name || '.' || method_name) FROM sales_audit_log_actions a "
                           "WHERE a.audit_log_id = l.id) AS actions FROM sales_audit_logs l ORDER BY execution_time")
    assert [(x["route"], x["http_status"]) for x in logs] == [("/api/v1/orders/export.csv", 200)] * 2
    assert all(x["user_id"] and x["role"] == "finance" and x["actions"] == ["OrderService.export_csv"] for x in logs)
    assert (logs[0]["extra"] or {}).get("query") is None
    q = logs[1]["extra"]["query"]
    assert "ship_to_place" in q and "search=97" in q and "source_type" in q


async def test_suggestions_answer_for_orders_and_transfers(two):
    r = await two.get("/api/v1/orders/suggest?field=source_order_number&q=97", headers=FINANCE)
    assert r.status_code == 200 and set(r.json()) == {"9701", "9702"}
    r = await two.get("/api/v1/transfers/suggest?field=transfer_number&q=", headers=FINANCE)
    assert r.status_code == 200 and r.json() == []
    r = await two.get("/api/v1/transfers/export.csv", headers=FINANCE)
    assert r.status_code == 200 and r.content.decode("utf-8-sig").startswith("ID,Transfer,")
