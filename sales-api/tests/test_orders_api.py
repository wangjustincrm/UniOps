"""P3 endpoints over HTTP, each permission denied and admitted: orders (list,
detail with lines / documents / timeline, the review worklist's filter and
re-check), manual orders (preview, create, submit) and the audited cursor
reset of a Shopify connection."""
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.deps import get_uow_factory
from app.main import app
from app.runtime import build_runtime, get_runtime
from tests.conftest import make_token
from tests.test_orders import env, shopify_order  # noqa: F401 — env is a fixture

GRANTS = {"op": {"sales.read", "sales.order.operate"}, "cfg": {"sales.read", "sales.config"}, "reader": {"sales.read"}}


def as_(role):
    return {"Authorization": f"Bearer {make_token(role)}"}


OPERATOR, CONFIG, READER, NOBODY = as_("op"), as_("cfg"), as_("reader"), as_("requester")


@pytest_asyncio.fixture
async def api(env):  # noqa: F811
    async def matrix(session, actor, key):  # noqa: ARG001
        return key in GRANTS.get(actor.role or "", set())
    env.factory.permissions = matrix
    runtime = build_runtime(settings, env.factory, shopify=env.shop)
    app.dependency_overrides[get_uow_factory] = lambda: env.factory
    app.dependency_overrides[get_runtime] = lambda: runtime
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.env = env
        yield c
    app.dependency_overrides.clear()


async def test_orders_list_detail_and_the_review_worklist(api):
    api.env.shop.orders = [shopify_order(1, 9301), shopify_order(2, 9302, lines=[
        {"id": 5, "sku": "NOPE", "variant_title": "Case", "quantity": 1, "price": "1", "requires_shipping": True}])]
    await api.env.pull()
    assert (await api.get("/api/v1/orders", headers=NOBODY)).status_code == 403
    r = (await api.get("/api/v1/orders?sort=order_number", headers=READER)).json()
    assert [(o["source_order_number"], o["fulfillment_status"], o["order_state"]) for o in r["items"]] == [
        ("9301", "Pending", "Active"), ("9302", "Review", "On Hold")]
    g = (await api.get("/api/v1/orders?group=fulfillment_status,partner_code&page_size=1", headers=READER)).json()
    assert [(x["key"], x["count"]) for x in g["groups"]] == [(["Pending", "SHOP"], 1), (["Review", "SHOP"], 1)]
    assert g["groups_truncated"] is False and len(g["items"]) == 1 and g["total"] == 2
    # the Total column's sums: over the whole result (not the page), per currency, and per group
    assert [(t["currency"], t["count"], t["sums"]["total_amount"]) for t in g["totals"]] == [("CAD", 2, "200.00")]
    assert [(t["count"], t["sums"]["total_amount"]) for t in g["groups"][0]["totals"]] == [(1, "100.00")]
    r = await api.get("/api/v1/orders?group=review_reason", headers=READER)
    assert r.status_code == 422 and r.json()["error"]["code"] == "query.not_groupable"
    assert "groups" not in (await api.get("/api/v1/orders", headers=READER)).json()  # only when asked
    review = (await api.get("/api/v1/orders?filter=fulfillment_status:eq:Review", headers=READER)).json()
    assert [o["source_order_number"] for o in review["items"]] == ["9302"]
    d = (await api.get(f"/api/v1/orders/{review['items'][0]['id']}", headers=READER)).json()
    assert d["lines"][0]["problem"] == "item not in the Item Master"
    assert [doc["kind"] for doc in d["documents"]] == ["SHOPIFY_ORDER_JSON"]
    oid = d["id"]
    assert (await api.post(f"/api/v1/orders/{oid}/resolve-review", headers=READER)).status_code == 403
    r = await api.post(f"/api/v1/orders/{oid}/resolve-review", headers=OPERATOR)
    assert r.status_code == 200 and r.json()["remaining"] and r.json()["order"]["fulfillment_status"] == "Review"


async def test_manual_order_preview_create_submit(api):
    body = {"partner_code": "MAN", "customer_reference": "phone-1",
            "ship_to": {"name": "A B", "phone": "514-555-0100", "address1": "1 rue", "city": "Montréal", "province": "QC", "zip": "h2x1y4"},
            "lines": [{"item_code": "CF0092", "quantity": 2, "unit": "case"}]}
    assert (await api.post("/api/v1/orders/manual/preview", json=body["lines"], headers=READER)).status_code == 403
    r = await api.post("/api/v1/orders/manual/preview", json=body["lines"], headers=OPERATOR)
    assert r.json() == [{"item_code": "CF0092", "quantity": "2", "unit": "CASE", "qty_ea": 12, "problem": None}]
    assert (await api.post("/api/v1/orders/manual", json=body, headers=READER)).status_code == 403
    o = (await api.post("/api/v1/orders/manual", json=body, headers=OPERATOR)).json()
    assert (o["fulfillment_status"], o["order_state"]) == ("Draft", "Draft")
    r = await api.post(f"/api/v1/orders/manual/{o['id']}/submit?version={o['version']}", headers=OPERATOR)
    assert r.status_code == 200 and r.json()["fulfillment_status"] == "Pending"
    r = await api.post(f"/api/v1/orders/manual/{o['id']}/submit?version={r.json()['version']}", headers=OPERATOR)
    assert r.status_code == 409 and r.json()["error"]["code"] == "order.not_draft"
    trail = (await api.get(f"/api/v1/orders/{o['id']}", headers=READER)).json()["timeline"]
    assert {e["reason"] for e in trail} >= {"manual order: create draft", "manual order: submit"}


async def test_the_cursor_is_read_only_and_reset_by_an_audited_action(api):
    cid = api.env.conn_id
    c = (await api.get(f"/api/v1/config/connections/{cid}", headers=READER)).json()
    assert c["cursor_updated_at"] and c["partner_code"] == "SHOP" and c["api_version"] == "2026-04"
    r = await api.put(f"/api/v1/config/connections/{cid}?version={c['version']}",
                      json={"cursor_updated_at": None}, headers=CONFIG)
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "cursor_updated_at"  # not editable
    body = {"cursor_updated_at": "2026-10-01T00:00:00+00:00"}
    assert (await api.post(f"/api/v1/config/connections/{cid}/reset-cursor", json=body, headers=READER)).status_code == 403
    r = await api.post(f"/api/v1/config/connections/{cid}/reset-cursor", json=body, headers=CONFIG)
    assert r.status_code == 200 and r.json()["cursor_updated_at"].startswith("2026-10-01")
    trail = (await api.get(f"/api/v1/config/connections/{cid}/audit", headers=READER)).json()
    assert trail[0]["reason"] == "reset cursor" and "cursor_updated_at" in trail[0]["after"]


async def test_a_manual_order_needs_the_ship_to_the_940_needs_field_by_field(api):
    base = {"partner_code": "MAN", "lines": [{"item_code": "CF0092", "quantity": 1, "unit": "EA"}]}
    r = await api.post("/api/v1/orders/manual", json=base, headers=OPERATOR)
    assert r.status_code == 422 and {e["field"] for e in r.json()["error"]["details"]["errors"]} == {"ship_to"}
    r = await api.post("/api/v1/orders/manual", json=base | {"ship_to": {"name": "A B", "city": "Laval", "province": "XX", "zip": "123"}},
                       headers=OPERATOR)
    errs = {e["field"]: e["message"] for e in r.json()["error"]["details"]["errors"]}
    assert r.status_code == 422 and set(errs) == {"ship_to.address1", "ship_to.phone", "ship_to.province", "ship_to.zip"}
    assert "A1A 1A1" in errs["ship_to.zip"] and "province code" in errs["ship_to.province"]
    r = await api.post("/api/v1/orders/manual", json=base | {"ship_to": {"name": "A B", "phone": "450-555-0100", "address1": "1 rue", "city": "Laval",
                                                                         "province": "qc", "zip": "h7n1a1"}}, headers=OPERATOR)
    assert r.status_code == 201 or r.status_code == 200, r.text
    assert r.json()["ship_to"] == {"country": "CA", "name": "A B", "phone": "450-555-0100", "address1": "1 rue", "city": "Laval",
                                   "province": "QC", "zip": "H7N 1A1"}
    r = await api.post("/api/v1/orders/manual", json=base | {"ship_to": {"country": "US", "name": "A B", "phone": "716-555-0100", "address1": "1 Main St",
                                                                         "city": "Buffalo", "province": "NY", "zip": "14201"}}, headers=OPERATOR)
    assert r.status_code in (200, 201)  # outside Canada: no Canadian rules


async def test_a_manual_order_postal_code_must_be_in_its_province_and_a_po_box_is_accepted(api):
    base = {"partner_code": "MAN", "lines": [{"item_code": "CF0092", "quantity": 1, "unit": "EA"}]}
    addr = {"name": "A B", "phone": "613-555-0100", "address1": "1 Main St", "city": "Ottawa", "province": "QC", "zip": "K1A 0B1"}
    r = await api.post("/api/v1/orders/manual", json=base | {"ship_to": addr}, headers=OPERATOR)
    errs = {e["field"]: e["message"] for e in r.json()["error"]["details"]["errors"]}
    assert r.status_code == 422 and set(errs) == {"ship_to.zip"} and "is in ON, not QC" in errs["ship_to.zip"]
    r = await api.post("/api/v1/orders/manual", json=base | {"ship_to": addr | {"province": "ON", "address2": "PO Box 12"}},
                       headers=OPERATOR)
    assert r.status_code in (200, 201), r.text  # a PO box is a warning on the form, never a refusal
    detail = (await api.get(f"/api/v1/orders/{r.json()['id']}", headers=READER)).json()
    assert detail["ship_to_flags"] == ["PO box: the carrier may not deliver there"]
