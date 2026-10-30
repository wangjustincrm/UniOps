"""Shopify integration (design §5.2, FR-OB-10): the Admin REST API, read-only
in P3 — shop info for the connection test, the order pages for intake and a
variant's barcode for item matching (read_products; FR-OB-02).
Writes (fulfillment, tracking, close) arrive in P5 behind the guard.

Authentication (PRD FR-OB-10): the client-credentials grant —
`<secret_name>_CLIENT_ID` / `_CLIENT_SECRET` are exchanged for a 24-hour
token, cached in memory and renewed 600 s before expiry. Only when the id /
secret are absent does the gateway use a static token in `<secret_name>` (a
fallback, logged once). Secret values never appear in a message or a log.

The host must be a `*.myshopify.com` store, so a token is never sent anywhere
else. The order query never asks for `customer` (FR-OB-10: without
read_customers Shopify fails the whole page).
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

RENEW_BEFORE_SECONDS = 600
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_STORE_HOST = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
_NEXT_LINK = re.compile(r'<[^>]*[?&]page_info=([^&>]+)[^>]*>;\s*rel="next"')

# What the order pages carry — explicitly, so `customer` is never requested.
ORDER_FIELDS = (
    "id", "name", "order_number", "created_at", "updated_at", "processed_at", "cancelled_at", "cancel_reason",
    "closed_at", "financial_status", "fulfillment_status", "currency", "current_total_price", "total_price",
    "subtotal_price", "total_tax", "total_discounts", "total_shipping_price_set", "email", "phone", "note",
    "tags", "test", "shipping_address", "billing_address", "line_items", "refunds", "shipping_lines",
)


class ShopifyError(Exception):
    """A Shopify call that did not succeed. Its text is safe to show."""


class ShopifyAuthFailed(ShopifyError):
    """The store refused the credentials. Latches the connection (FR-OB-50)."""


class ShopifyRefused(ShopifyError):
    """Refused before any request: not a store host, or no credentials."""


@dataclass(frozen=True)
class OrdersPage:
    orders: list[dict[str, Any]]
    next_page_info: str | None


class ShopifyGateway(Protocol):
    async def shop(self, conn: Any) -> dict[str, Any]: ...
    async def variant(self, conn: Any, variant_id: str) -> dict[str, Any]: ...
    async def orders_page(self, conn: Any, *, updated_at_min: str | None = None,
                          page_info: str | None = None, limit: int = 50) -> OrdersPage: ...


READS = ("shop", "orders_page", "variant")
WRITES: tuple[str, ...] = ()  # P5: fulfillment, tracking, close — behind SALES_PARTNER_WRITES_ENABLED


def guarded(switch: str) -> dict[str, str]:
    return {m: switch for m in WRITES}


@dataclass
class _Token:
    value: str
    expires_at: float  # monotonic; inf for a static token


class HttpShopify:
    def __init__(self, *, secret: Callable[[str], str | None], transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.secret = secret
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
            try:
                r = await c.get(f"https://{host}/admin/api/{version}/{path}", params=params,
                                headers={"X-Shopify-Access-Token": token, "Accept": "application/json"})
            except httpx.HTTPError as e:
                raise ShopifyError(readable(e)) from e
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
        return OrdersPage(r.json().get("orders", []), m.group(1) if m else None)


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
        return OrdersPage(list(rows), nxt)
