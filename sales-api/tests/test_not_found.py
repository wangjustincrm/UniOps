"""One 404 (stage 4): whatever is not there, the code is entity.not_found, with what and which in the details."""
import re
from pathlib import Path

import pytest

import app as package
from tests.conftest import make_token
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_orders_api import GRANTS, api  # noqa: F401 — api is a fixture

ROOT = Path(package.__path__[0])
# Not "this record is not there": a kind of record the address names that does not exist, and a document that exists but keeps no bytes.
OWN = {("services/record_files.py", "record.kind"), ("services/operations.py", "document.no_content")}
GONE = 99_999
CASES = [("POST", "/orders/{n}/amend?version=1", {"reason": "wrong item", "lines": [{"id": 1, "item_code": "CF0092", "ordered_qty": "1"}]}, "Order"),
         ("POST", "/orders/shipments/{n}/shopify-retry", None, "Shipment"),
         ("POST", "/orders/shipments/{n}/rows/{n}/resolve", {"order_line_id": None, "reason": "a sample"}, "ShipmentRow"),
         ("POST", "/orders/{n}/reorder", None, "Order"),
         ("PUT", "/orders/{n}/returned?version=1", {"reason": "refused", "lines": [{"id": 1, "returned_ea": 1}]}, "Order"),
         ("POST", "/invoices/confirm", {"order_id": GONE, "version": 1, "confirmed_date": "2026-11-05"}, "Order"),
         ("POST", "/invoices/returns", {"order_id": GONE, "version": 1, "kind": "Returned", "reason": "damaged", "lines": [{"order_line_id": 1, "qty_ea": 1}]}, "Order"),
         ("PUT", "/invoices/{n}?version=1", {"lines": []}, "Invoice"),
         ("GET", "/inventory/counts/{n}", None, "StockCount"),
         ("POST", "/inventory/adjust", {"item_code": "NOPE", "lot": None, "qty_ea": 1, "reason": "a count"}, "Item"),
         ("GET", "/attachments/order/{n}", None, "Order"), ("GET", "/attachments/transfer/{n}", None, "Transfer"),
         ("POST", "/attachments/order/{n}/5/remove", {"reason": "wrong file"}, "Order"),
         ("GET", "/orders/{n}", None, "Order"), ("GET", "/invoices/{n}", None, "Invoice"), ("GET", "/transfers/{n}", None, "Transfer")]


@pytest.mark.parametrize(("method", "path", "body", "entity"), CASES, ids=[f"{m} {p}" for m, p, _, _ in CASES])
async def test_what_is_not_there_is_the_one_404_for_who_may_and_403_for_who_may_not(api, method, path, body, entity):  # noqa: F811
    url = "/api/v1" + path.format(n=GONE)
    GRANTS["everything"] = {"sales.read", "sales.order.operate", "sales.invoice.manage", "sales.transfer.manage", "sales.inventory.adjust", "sales.file.retry"}
    GRANTS["nothing"] = set()
    r = await api.request(method, url, json=body, headers={"Authorization": f"Bearer {make_token('everything')}"})
    assert r.status_code == 404, r.text
    e = r.json()["error"]
    assert e["code"] == "entity.not_found" and e["details"]["entity"] == entity and e["message"]
    r = await api.request(method, url, json=body, headers={"Authorization": f"Bearer {make_token('nothing')}"})
    assert r.status_code == 403  # and nothing is said of whether it is there


def test_no_404_is_made_by_hand():
    by_hand = set()
    for p in ROOT.rglob("*.py"):
        if p.name == "errors.py":
            continue
        for m in re.finditer(r'BusinessError\(\s*"([a-z0-9_.]+)"(?:(?!BusinessError).)*?status_code=404', p.read_text(), re.S):
            by_hand.add((str(p.relative_to(ROOT)), m.group(1)))
    assert by_hand == OWN
    codes = {c for p in ROOT.rglob("*.py") for c in re.findall(r'"([a-z_]+\.not_found)"', p.read_text())}
    assert codes == {"entity.not_found"}
