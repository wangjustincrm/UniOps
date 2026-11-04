"""Shopify integration (design §5.2, FR-OB-10): the Admin REST API, read-only
in P3 — shop info for the connection test, the order pages for intake and a
variant's barcode for item matching (read_products; FR-OB-02).

P5 (FR-OB-11 / 11a): the order's fulfillment orders and fulfillments (reads),
and three writes behind the guard — create a fulfillment with tracking, add
tracking to an existing fulfillment, close (archive) the order. A write is
made once; a refused permission (missing scope) is `ShopifyForbidden`, never
the login latch.

Authentication (PRD FR-OB-10): the client-credentials grant —
`<secret_name>_CLIENT_ID` / `_CLIENT_SECRET` are exchanged for a 24-hour
token, cached in memory and renewed 600 s before expiry. Only when the id /
secret are absent does the gateway use a static token in `<secret_name>` (a
fallback, logged once). Secret values never appear in a message or a log.

The host must be a `*.myshopify.com` store, so a token is never sent anywhere
else. The order query never asks for `customer` (FR-OB-10: without
read_customers Shopify fails the whole page).

Finance (user 2026-11-03, read_orders): one order and its transactions. No
card data is kept anywhere: every JSON this client returns has the card
fields stripped (`strip_card_data`) — a transaction's `payment_details`
(BIN, masked number, holder, expiry, AVS / CVV results), `receipt` and any
`credit_card_*` key — so neither a stored page nor a payment row carries them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

RENEW_BEFORE_SECONDS = 600
# A read answered 429 (the REST bucket is full) waits Retry-After and tries again, a few times.
RATE_LIMIT_RETRIES = 4
MAX_RETRY_AFTER = 10.0
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_STORE_HOST = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
_NEXT_LINK = re.compile(r'<[^>]*[?&]page_info=([^&>]+)[^>]*>;\s*rel="next"')

# What the order pages carry — explicitly, so `customer` is never requested.
ORDER_FIELDS = (
    "id", "name", "order_number", "created_at", "updated_at", "processed_at", "cancelled_at", "cancel_reason",
    "closed_at", "financial_status", "fulfillment_status", "currency", "current_total_price", "total_price",
    "subtotal_price", "total_tax", "total_discounts", "total_shipping_price_set", "email", "phone", "note",
    "tags", "test", "shipping_address", "billing_address", "line_items", "refunds", "shipping_lines",
    # finance (user 2026-11-03)
    "source_name", "payment_gateway_names", "discount_codes", "tax_lines",
)

# Card data, never kept: dropped wherever it appears in a response.
CARD_KEYS = frozenset({"payment_details", "receipt", "avs_result_code", "cvv_result_code", "buyer_action_info",
                       "extended_authorization_attributes", "payment_method_name"})


def is_card_key(key: str) -> bool:
    """A key that holds card data: the CARD_KEYS and any `credit_card_*` (never `gift_card`: FR-OB-11b reads it)."""
    return key in CARD_KEYS or key.startswith("credit_card")


def strip_card_data(value: Any) -> Any:
    """The JSON without card data, at any depth."""
    if isinstance(value, dict):
        return {k: strip_card_data(v) for k, v in value.items() if not is_card_key(k)}
    if isinstance(value, list):
        return [strip_card_data(v) for v in value]
    return value


def page_bytes(page: dict[str, Any]) -> bytes:
    """An orders page as it is stored: the same bytes, so the same sha256, for the same page."""
    return json.dumps(page, ensure_ascii=False, sort_keys=True).encode()


class ShopifyError(Exception):
    """A Shopify call that did not succeed. Its text is safe to show."""


class ShopifyAuthFailed(ShopifyError):
    """The store refused the credentials. Latches the connection (FR-OB-50)."""


class ShopifyRefused(ShopifyError):
    """Refused before any request: not a store host, or no credentials."""


class ShopifyForbidden(ShopifyError):
    """A write the app has no permission (scope) for — e.g. closing an order
    without write_orders. Not a login failure: the connection is not latched."""


@dataclass(frozen=True)
class OrdersPage:
    orders: list[dict[str, Any]]
    next_page_info: str | None


class ShopifyGateway(Protocol):
    async def shop(self, conn: Any) -> dict[str, Any]: ...
    async def variant(self, conn: Any, variant_id: str) -> dict[str, Any]: ...
    async def orders_page(self, conn: Any, *, updated_at_min: str | None = None,
                          page_info: str | None = None, limit: int = 50) -> OrdersPage: ...


    async def order(self, conn: Any, order_id: str) -> dict[str, Any]: ...
    async def transactions(self, conn: Any, order_id: str) -> list[dict[str, Any]]: ...
    async def fulfillment_orders(self, conn: Any, order_id: str) -> list[dict[str, Any]]: ...
    async def fulfillments(self, conn: Any, order_id: str) -> list[dict[str, Any]]: ...
    async def locations(self, conn: Any) -> list[dict[str, Any]]: ...
    async def create_fulfillment(self, conn: Any, items: list[dict[str, Any]], tracking: dict[str, Any] | None,
                                 notify: bool) -> dict[str, Any]: ...
    async def update_tracking(self, conn: Any, fulfillment_id: str, tracking: dict[str, Any], notify: bool) -> dict[str, Any]: ...
    async def close_order(self, conn: Any, order_id: str) -> dict[str, Any]: ...


READS = ("shop", "orders_page", "order", "transactions", "variant", "fulfillment_orders", "fulfillments", "locations")
# P5: behind SALES_PARTNER_WRITES_ENABLED, and only for a LIVE connection.
WRITES: tuple[str, ...] = ("create_fulfillment", "update_tracking", "close_order")


def guarded(switch: str) -> dict[str, str]:
    return {m: switch for m in WRITES}


@dataclass
class _Token:
    value: str
    expires_at: float  # monotonic; inf for a static token


class HttpShopify:
    def __init__(self, *, secret: Callable[[str], str | None], transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        self.secret = secret
        self.sleep = sleep
        self.transport = transport
        self.clock = clock
        self._tokens: dict[tuple[str, str], _Token] = {}
        self._fallback_logged: set[str] = set()

    # ── auth ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _host(conn: Any) -> str:
        host = (getattr(conn, "host", None) or "").strip().lower()
        if not _STORE_HOST.match(host):
            raise ShopifyRefused("the host must be the store's own <name>.myshopify.com address")
        return host

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT)

    async def _token(self, conn: Any, host: str) -> str:
        name = getattr(conn, "secret_name", None) or ""
        cid, csec = self.secret(f"{name}_CLIENT_ID"), self.secret(f"{name}_CLIENT_SECRET")
        if not (cid and csec):
            static = self.secret(name) if name else None
            if not static:
                raise ShopifyRefused(f"no credentials: set {name or '<secret_name>'}_CLIENT_ID and _CLIENT_SECRET "
                                     "in the server environment")
            if name not in self._fallback_logged:
                logger.warning("shopify %s: no client id/secret under %s_CLIENT_*; using the static token fallback",
                               host, name)
                self._fallback_logged.add(name)
            return static
        key = (host, cid)
        cached = self._tokens.get(key)
        if cached and cached.expires_at - self.clock() > RENEW_BEFORE_SECONDS:
            return cached.value
        async with self._client() as c:
            try:
                r = await c.post(f"https://{host}/admin/oauth/access_token",
                                 json={"client_id": cid, "client_secret": csec, "grant_type": "client_credentials"})
            except httpx.HTTPError as e:
                raise ShopifyError(readable(e)) from e
        if r.status_code in (400, 401, 403):
            raise ShopifyAuthFailed(f"the store refused the app credentials (HTTP {r.status_code})")
        if r.status_code != 200:
            raise ShopifyError(f"the token request failed (HTTP {r.status_code})")
        body = r.json()
        self._tokens[key] = _Token(body["access_token"], self.clock() + float(body.get("expires_in", 86399)))
        return body["access_token"]

    async def _get(self, conn: Any, path: str, params: dict[str, Any]) -> httpx.Response:
        host = self._host(conn)
        version = getattr(conn, "api_version", None)
        if not version:
            raise ShopifyRefused("the connection has no API version")
        token = await self._token(conn, host)
        async with self._client() as c:
            for attempt in range(RATE_LIMIT_RETRIES + 1):
                try:
                    r = await c.get(f"https://{host}/admin/api/{version}/{path}", params=params,
                                    headers={"X-Shopify-Access-Token": token, "Accept": "application/json"})
                except httpx.HTTPError as e:
                    raise ShopifyError(readable(e)) from e
                if r.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                    break
                await self.sleep(_retry_after(r))  # the store's bucket refills at 2 calls a second
        if r.status_code in (401, 403):
            self._tokens = {k: v for k, v in self._tokens.items() if k[0] != host}  # a fresh token next time
            raise ShopifyAuthFailed(f"the store refused the request (HTTP {r.status_code})")
        if r.status_code == 404:
            raise ShopifyError(f"the store has nothing at {path} (HTTP 404)")
        if r.status_code == 429:
            raise ShopifyError("the store's API rate limit was reached; the next cycle carries on")
        if r.status_code != 200:
            raise ShopifyError(f"the store answered HTTP {r.status_code} for {path}")
        return r

    async def _post(self, conn: Any, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """One write. 401 is a login failure; 403 a missing permission
        (ShopifyForbidden, with Shopify's own reason); 422 a refused request."""
        host = self._host(conn)
        version = getattr(conn, "api_version", None)
        if not version:
            raise ShopifyRefused("the connection has no API version")
        token = await self._token(conn, host)
        async with self._client() as c:
            try:
                r = await c.post(f"https://{host}/admin/api/{version}/{path}", json=body,
                                 headers={"X-Shopify-Access-Token": token, "Accept": "application/json"})
            except httpx.HTTPError as e:
                raise ShopifyError(readable(e)) from e
        if r.status_code == 401:
            self._tokens = {k: v for k, v in self._tokens.items() if k[0] != host}
            raise ShopifyAuthFailed(f"the store refused the request (HTTP {r.status_code})")
        if r.status_code == 403:
            raise ShopifyForbidden(f"the app has no permission for {path} (HTTP 403): {_errors(r)}")
        if r.status_code in (200, 201):
            return r.json()
        raise ShopifyError(f"the store answered HTTP {r.status_code} for {path}: {_errors(r)}")

    # ── reads ────────────────────────────────────────────────────────────────
    async def shop(self, conn: Any) -> dict[str, Any]:
        r = await self._get(conn, "shop.json", {"fields": "name,domain,myshopify_domain,currency,iana_timezone,plan_name"})
        return r.json()["shop"]

    async def variant(self, conn: Any, variant_id: str) -> dict[str, Any]:
        """One product variant — the order line carries no barcode."""
        if not str(variant_id).isdigit():
            raise ShopifyRefused("a variant id is digits only")
        r = await self._get(conn, f"variants/{variant_id}.json", {"fields": "id,product_id,sku,barcode"})
        return r.json()["variant"]

    async def orders_page(self, conn: Any, *, updated_at_min: str | None = None,
                          page_info: str | None = None, limit: int = 50) -> OrdersPage:
        """One page, oldest update first. With `page_info` Shopify accepts only
        limit and fields beside it (the cursor is in the page_info)."""
        params: dict[str, Any] = {"limit": limit, "fields": ",".join(ORDER_FIELDS)}
        if page_info:
            params["page_info"] = page_info
        else:
            params |= {"status": "any", "order": "updated_at asc"}
            if updated_at_min:
                params["updated_at_min"] = updated_at_min
        r = await self._get(conn, "orders.json", params)
        m = _NEXT_LINK.search(r.headers.get("link", ""))
        return OrdersPage(strip_card_data(r.json().get("orders", [])), m.group(1) if m else None)

    async def order(self, conn: Any, order_id: str) -> dict[str, Any]:
        """One order, the same fields as the pages (a finance refresh of an order already here)."""
        r = await self._get(conn, f"orders/{_digits(order_id)}.json", {"fields": ",".join(ORDER_FIELDS)})
        return strip_card_data(r.json()["order"])

    async def transactions(self, conn: Any, order_id: str) -> list[dict[str, Any]]:
        """The order's payments: sales, authorizations, captures, refunds, voids (read_orders)."""
        r = await self._get(conn, f"orders/{_digits(order_id)}/transactions.json", {})
        return strip_card_data(r.json().get("transactions", []))

    async def fulfillment_orders(self, conn: Any, order_id: str) -> list[dict[str, Any]]:
        r = await self._get(conn, f"orders/{_digits(order_id)}/fulfillment_orders.json", {})
        return r.json().get("fulfillment_orders", [])

    async def fulfillments(self, conn: Any, order_id: str) -> list[dict[str, Any]]:
        r = await self._get(conn, f"orders/{_digits(order_id)}/fulfillments.json", {})
        return r.json().get("fulfillments", [])

    async def locations(self, conn: Any) -> list[dict[str, Any]]:
        r = await self._get(conn, "locations.json", {})
        return r.json().get("locations", [])

    # ── writes (guarded; made once) ──────────────────────────────────────────
    async def create_fulfillment(self, conn: Any, items: list[dict[str, Any]], tracking: dict[str, Any] | None,
                                 notify: bool) -> dict[str, Any]:
        """`items`: [{"fulfillment_order_id", "fulfillment_order_line_items": [{"id", "quantity"}]}]."""
        body: dict[str, Any] = {"line_items_by_fulfillment_order": items, "notify_customer": notify}
        if tracking:
            body["tracking_info"] = tracking
        return (await self._post(conn, "fulfillments.json", {"fulfillment": body}))["fulfillment"]

    async def update_tracking(self, conn: Any, fulfillment_id: str, tracking: dict[str, Any], notify: bool) -> dict[str, Any]:
        return (await self._post(conn, f"fulfillments/{_digits(fulfillment_id)}/update_tracking.json",
                                 {"fulfillment": {"tracking_info": tracking, "notify_customer": notify}}))["fulfillment"]

    async def close_order(self, conn: Any, order_id: str) -> dict[str, Any]:
        return (await self._post(conn, f"orders/{_digits(order_id)}/close.json", {}))["order"]


def _retry_after(r: httpx.Response) -> float:
    try:
        return min(max(float(r.headers.get("Retry-After", "1")), 0.5), MAX_RETRY_AFTER)
    except ValueError:
        return 1.0


def _digits(v: Any) -> str:
    if not str(v).isdigit():
        raise ShopifyRefused("a Shopify id is digits only")
    return str(v)


def _errors(r: httpx.Response) -> str:
    """Shopify's error text, short (it names the missing scope)."""
    try:
        e = r.json().get("errors")
    except ValueError:
        return "no detail"
    return str(e)[:300] if e else "no detail"


def summary(method: str, args: tuple, kwargs: dict) -> dict[str, Any]:
    """The "would have written" record of a refused write: ids only."""
    conn = args[0] if args else kwargs.get("conn")
    out: dict[str, Any] = {"connection": getattr(conn, "id", None), "mode": getattr(conn, "mode", None)}
    if method == "create_fulfillment":
        items = args[1] if len(args) > 1 else kwargs.get("items", [])
        out["fulfillment_orders"] = [i.get("fulfillment_order_id") for i in items]
    elif method in ("update_tracking", "close_order"):
        out["id"] = str(args[1] if len(args) > 1 else kwargs.get("fulfillment_id") or kwargs.get("order_id"))
    return out


def readable(e: BaseException) -> str:
    if isinstance(e, ShopifyError):
        return str(e)
    if isinstance(e, httpx.TimeoutException):
        return "The store did not answer in time."
    if isinstance(e, httpx.ConnectError):
        return "The store could not be reached."
    return f"The Shopify request failed ({type(e).__name__})."


@dataclass
class FakeShopify:
    """In-memory store for tests: pages of orders keyed by request."""
    shop_info: dict[str, Any] = field(default_factory=lambda: {"name": "Test store", "currency": "CAD"})
    orders: list[dict[str, Any]] = field(default_factory=list)
    variants: dict[str, dict[str, Any]] = field(default_factory=dict)  # variant id → {"barcode": …}
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail: dict[str, BaseException] = field(default_factory=dict)
    page_size: int | None = None
    # finance: per Shopify order id (returned as the real client would: card data stripped)
    transactions_by_order: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # P5: per Shopify order id
    fulfillment_orders_by_order: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    fulfillments_by_order: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    store_locations: list[dict[str, Any]] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)

    async def shop(self, conn: Any) -> dict[str, Any]:
        self.calls.append(("shop", {}))
        if "shop" in self.fail:
            raise self.fail["shop"]
        return self.shop_info

    async def variant(self, conn: Any, variant_id: str) -> dict[str, Any]:
        self.calls.append(("variant", {"variant_id": str(variant_id)}))
        if "variant" in self.fail:
            raise self.fail["variant"]
        if str(variant_id) not in self.variants:
            raise ShopifyError(f"the store has nothing at variants/{variant_id}.json (HTTP 404)")
        return {"id": int(variant_id), **self.variants[str(variant_id)]}

    async def orders_page(self, conn: Any, *, updated_at_min: str | None = None,
                          page_info: str | None = None, limit: int = 50) -> OrdersPage:
        self.calls.append(("orders_page", {"updated_at_min": updated_at_min, "page_info": page_info, "limit": limit}))
        if "orders_page" in self.fail:
            raise self.fail["orders_page"]
        size = self.page_size or limit
        if page_info is None:
            rows = sorted((o for o in self.orders if updated_at_min is None or (o.get("updated_at") or "") >= updated_at_min),
                          key=lambda o: (o.get("updated_at") or "", o.get("id") or 0))
            self._pending = rows
            start = 0
        else:
            start = int(page_info)
        rows = self._pending[start:start + size]
        nxt = str(start + size) if start + size < len(self._pending) else None
        return OrdersPage(strip_card_data(list(rows)), nxt)

    async def order(self, conn: Any, order_id: str) -> dict[str, Any]:
        self.calls.append(("order", {"order_id": str(order_id)}))
        if "order" in self.fail:
            raise self.fail["order"]
        found = next((o for o in self.orders if str(o.get("id")) == str(order_id)), None)
        if found is None:
            raise ShopifyError(f"the store has nothing at orders/{order_id}.json (HTTP 404)")
        return strip_card_data(found)

    async def transactions(self, conn: Any, order_id: str) -> list[dict[str, Any]]:
        self.calls.append(("transactions", {"order_id": str(order_id)}))
        if "transactions" in self.fail:
            raise self.fail["transactions"]
        return strip_card_data(self.transactions_by_order.get(str(order_id), []))

    async def fulfillment_orders(self, conn: Any, order_id: str) -> list[dict[str, Any]]:
        self.calls.append(("fulfillment_orders", {"order_id": str(order_id)}))
        return self.fulfillment_orders_by_order.get(str(order_id), [])

    async def fulfillments(self, conn: Any, order_id: str) -> list[dict[str, Any]]:
        self.calls.append(("fulfillments", {"order_id": str(order_id)}))
        if "fulfillments" in self.fail:
            raise self.fail["fulfillments"]
        return self.fulfillments_by_order.get(str(order_id), [])

    async def locations(self, conn: Any) -> list[dict[str, Any]]:
        self.calls.append(("locations", {}))
        return self.store_locations

    async def create_fulfillment(self, conn: Any, items: list[dict[str, Any]], tracking: dict[str, Any] | None,
                                 notify: bool) -> dict[str, Any]:
        self.calls.append(("create_fulfillment", {"items": items, "tracking": tracking, "notify": notify}))
        if "create_fulfillment" in self.fail:
            raise self.fail["create_fulfillment"]
        order_id = next((oid for oid, fos in self.fulfillment_orders_by_order.items()
                         if any(fo["id"] == items[0]["fulfillment_order_id"] for fo in fos)), "?")
        f = {"id": 9000 + len(self.calls), "order_id": order_id, "status": "success",
             "tracking_number": (tracking or {}).get("number")}
        self.fulfillments_by_order.setdefault(order_id, []).append(f)
        return f

    async def update_tracking(self, conn: Any, fulfillment_id: str, tracking: dict[str, Any], notify: bool) -> dict[str, Any]:
        self.calls.append(("update_tracking", {"fulfillment_id": str(fulfillment_id), "tracking": tracking, "notify": notify}))
        if "update_tracking" in self.fail:
            raise self.fail["update_tracking"]
        for fs in self.fulfillments_by_order.values():
            for f in fs:
                if str(f["id"]) == str(fulfillment_id):
                    f["tracking_number"] = tracking.get("number")
                    return f
        raise ShopifyError(f"the store has nothing at fulfillments/{fulfillment_id} (HTTP 404)")

    async def close_order(self, conn: Any, order_id: str) -> dict[str, Any]:
        self.calls.append(("close_order", {"order_id": str(order_id)}))
        if "close_order" in self.fail:
            raise self.fail["close_order"]
        self.closed.append(str(order_id))
        return {"id": int(order_id), "closed_at": "2026-11-02T12:00:00Z"}
