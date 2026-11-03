"""The real Shopify client against httpx.MockTransport — never the real
store from tests. Client-credentials token: obtained once, cached, renewed
600 s before expiry; the static token only as a logged fallback; the order
query never asks for `customer` (FR-OB-10); secrets never in a message."""
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.integrations.shopify import HttpShopify, ShopifyAuthFailed, ShopifyError, ShopifyRefused

CONN = SimpleNamespace(host="example.myshopify.com", api_version="2026-04", secret_name="SALES_SECRET_SHOPIFY")
SECRETS = {"SALES_SECRET_SHOPIFY_CLIENT_ID": "cid-123", "SALES_SECRET_SHOPIFY_CLIENT_SECRET": "shpss_SECRET",
           "SALES_SECRET_SHOPIFY": "shpat_STATIC"}


class Store:
    def __init__(self, *, expires_in=86399, status=200):
        self.requests: list[httpx.Request] = []
        self.grants = 0
        self.expires_in = expires_in
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/admin/oauth/access_token":
            self.grants += 1
            return httpx.Response(200, json={"access_token": f"tok-{self.grants}", "expires_in": self.expires_in})
        if self.status != 200:
            return httpx.Response(self.status, json={"errors": "nope"})
        if request.url.path.endswith("/variants/48678343770418.json"):
            return httpx.Response(200, json={"variant": {"id": 48678343770418, "sku": "CF0092", "barcode": "062639300012"}})
        if "/variants/" in request.url.path:
            return httpx.Response(404, json={"errors": "Not Found"})
        if request.url.path.endswith("/shop.json"):
            return httpx.Response(200, json={"shop": {"name": "NIURISS", "currency": "CAD"}})
        if "page_info" in request.url.params:
            return httpx.Response(200, json={"orders": [{"id": 3}]})
        return httpx.Response(200, json={"orders": [{"id": 1}, {"id": 2}]}, headers={
            "link": '<https://example.myshopify.com/admin/api/2026-04/orders.json?limit=2&page_info=abc123>; rel="next"'})


def client(store, secrets=SECRETS, clock=None):
    return HttpShopify(secret=secrets.get, transport=httpx.MockTransport(store), **({"clock": clock} if clock else {}))


async def test_client_credentials_token_is_cached_and_renewed_before_expiry():
    store, now = Store(expires_in=3600), [0.0]
    gw = client(store, clock=lambda: now[0])
    await gw.shop(CONN)
    await gw.shop(CONN)
    assert store.grants == 1  # cached
    grant = store.requests[0]
    assert grant.method == "POST" and b'"grant_type": "client_credentials"' in grant.content.replace(b'":"', b'": "')
    now[0] = 3600 - 599  # less than 600 s left: renewed
    await gw.shop(CONN)
    assert store.grants == 2
    assert store.requests[-1].headers["X-Shopify-Access-Token"] == "tok-2"


async def test_the_static_token_is_only_a_logged_fallback(caplog):
    store = Store()
    gw = client(store, secrets={"SALES_SECRET_SHOPIFY": "shpat_STATIC"})
    with caplog.at_level(logging.WARNING):
        await gw.shop(CONN)
        await gw.shop(CONN)
    assert store.grants == 0 and store.requests[0].headers["X-Shopify-Access-Token"] == "shpat_STATIC"
    assert sum("static token fallback" in r.message for r in caplog.records) == 1
    assert all("shpat_STATIC" not in r.getMessage() for r in caplog.records)


async def test_the_order_query_never_asks_for_customer_and_follows_link_pages():
    store = Store()
    gw = client(store)
    first = await gw.orders_page(CONN, updated_at_min="2026-10-28T12:00:00+00:00", limit=2)
    assert [o["id"] for o in first.orders] == [1, 2] and first.next_page_info == "abc123"
    q = store.requests[-1].url.params
    assert "customer" not in q["fields"].split(",") and q["status"] == "any" and q["order"] == "updated_at asc"
    assert q["updated_at_min"] == "2026-10-28T12:00:00+00:00"
    second = await gw.orders_page(CONN, page_info=first.next_page_info, limit=2)
    q = store.requests[-1].url.params
    assert [o["id"] for o in second.orders] == [3] and second.next_page_info is None
    assert set(q) == {"limit", "fields", "page_info"}  # Shopify accepts nothing else beside page_info


async def test_refusals_and_auth_failures_never_carry_a_secret():
    with pytest.raises(ShopifyRefused, match="myshopify.com"):
        await client(Store()).shop(SimpleNamespace(**{**vars(CONN), "host": "evil.example.com"}))
    with pytest.raises(ShopifyRefused, match="no credentials"):
        await client(Store(), secrets={}).shop(CONN)
    with pytest.raises(ShopifyAuthFailed) as e:
        await client(Store(status=401)).shop(CONN)
    assert "shpss_SECRET" not in str(e.value) and "cid-123" not in str(e.value) and "tok-" not in str(e.value)


async def test_a_variant_is_read_for_its_barcode_and_a_missing_one_is_a_readable_error():
    store = Store()
    gw = client(store)
    v = await gw.variant(CONN, "48678343770418")
    assert v["barcode"] == "062639300012"
    req = store.requests[-1]
    assert (req.method, req.url.path) == ("GET", "/admin/api/2026-04/variants/48678343770418.json")
    assert req.url.params["fields"] == "id,product_id,sku,barcode" and req.headers["X-Shopify-Access-Token"] == "tok-1"
    with pytest.raises(ShopifyError, match=r"nothing at variants/1\.json \(HTTP 404\)"):
        await gw.variant(CONN, "1")
    with pytest.raises(ShopifyRefused):
        await gw.variant(CONN, "1/../../shop")  # only digits reach the URL
    assert len(store.requests) == 3  # token + two reads; the refused id sent nothing


CARD_TX = {"id": 7001, "kind": "sale", "gateway": "shopify_payments", "status": "success", "amount": "100.00",
           "currency": "CAD", "payment_id": "c1.1", "receipt": {"payment_id": "x"},
           "payment_details": {"credit_card_bin": "424242", "credit_card_number": "•••• •••• •••• 4242",
                               "credit_card_name": "A B", "credit_card_expiration_month": 1, "avs_result_code": "Y"},
           "credit_card_company": "Visa"}


async def test_finance_reads_strip_card_data_everywhere_and_keep_gift_card():
    def store(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86399})
        if request.url.path.endswith("/orders/1/transactions.json"):
            return httpx.Response(200, json={"transactions": [CARD_TX]})
        order = {"id": 1, "line_items": [{"id": 11, "gift_card": False}],
                 "refunds": [{"id": 9, "transactions": [CARD_TX | {"id": 7002, "kind": "refund"}]}]}
        if request.url.path.endswith("/orders/1.json"):
            assert "source_name" in request.url.params["fields"] and "customer" not in request.url.params["fields"]
            return httpx.Response(200, json={"order": order})
        return httpx.Response(200, json={"orders": [order]})
    gw = client(store)
    for got in (await gw.transactions(CONN, "1"), await gw.order(CONN, "1"), (await gw.orders_page(CONN, updated_at_min="x")).orders):
        text = str(got)
        for banned in ("payment_details", "credit_card", "424242", "4242", "receipt", "avs_result_code"):
            assert banned not in text, banned
    [tx] = await gw.transactions(CONN, "1")
    assert (tx["id"], tx["kind"], tx["amount"], tx["payment_id"]) == (7001, "sale", "100.00", "c1.1")  # the rest is kept
    assert (await gw.order(CONN, "1"))["line_items"][0]["gift_card"] is False  # FR-OB-11b reads it
    with pytest.raises(ShopifyRefused):
        await gw.transactions(CONN, "1/../2")


async def test_a_full_rate_limit_bucket_is_waited_out_then_given_up():
    calls, waits = [], []

    def store(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86399})
        calls.append(request.url.path)
        if len(calls) <= 2:
            return httpx.Response(429, headers={"Retry-After": "2.0"}, json={"errors": "Exceeded 2 calls per second"})
        return httpx.Response(200, json={"transactions": [{"id": 1, "kind": "sale"}]})

    async def sleep(s):
        waits.append(s)
    gw = HttpShopify(secret=SECRETS.get, transport=httpx.MockTransport(store), sleep=sleep)
    assert (await gw.transactions(CONN, "5"))[0]["id"] == 1 and waits == [2.0, 2.0]
    calls.clear()
    waits.clear()
    always = HttpShopify(secret=SECRETS.get, sleep=sleep, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"access_token": "t", "expires_in": 86399}) if r.url.path.endswith("access_token")
        else httpx.Response(429, json={})))
    with pytest.raises(ShopifyError, match="rate limit"):
        await always.transactions(CONN, "5")
    assert len(waits) == 4  # RATE_LIMIT_RETRIES, then the error
