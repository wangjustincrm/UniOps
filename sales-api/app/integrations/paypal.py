"""PayPal integration (Finance, user 2026-11-19): the Transaction Search and Balances APIs, read-only.

Three calls, nothing else (`REQUESTS`, refused before sending otherwise):

- `POST /v1/oauth2/token` — client credentials (the REST app's Client ID and Secret, HTTP Basic) for a
  bearer token. The token is kept in memory until a minute before it expires; it is never logged.
- `GET /v1/reporting/transactions` — the account's transactions between two times (≤ 31 days per call),
  500 per page. PayPal shows a transaction a few hours after it happens (`last_refreshed_datetime`),
  and keeps about three years.
- `GET /v1/reporting/balances` — the balance now, per currency.

The REST app needs the "Transaction Search" feature (developer.paypal.com → Apps & Credentials → the
app → Features); without it PayPal answers 403, which latches the connection like a refused login.

Credentials: the connection's `username` is the Client ID (not a secret); the Secret is read from the
server environment under the connection's `secret_name`. The host must be `api-m.paypal.com` (or the
sandbox `api-m.sandbox.paypal.com`).

No personal data is kept: of each transaction only `transaction_info` is read, reduced to `TX_KEYS`
(`slim_transaction`) — the payer's name, e-mail, address, phone and PayPal account id, the shipping
address, the cart and the payer's note never leave here.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

HOSTS = ("api-m.paypal.com", "api-m.sandbox.paypal.com")
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
MAX_DAYS = 31          # the transaction search's widest window
PAGE_SIZE = 500        # its largest page
RETRIES = 3            # a 5xx / 429 / timeout is tried again, with a pause
TOKEN_SLACK = 60       # seconds: a token is renewed this long before it expires
REQUESTS = (("POST", "/v1/oauth2/token"), ("GET", "/v1/reporting/transactions"), ("GET", "/v1/reporting/balances"))

# What is kept of a transaction (`transaction_info`): ids, what kind of event, status, money, dates and the
# merchant's own references (invoice id, custom field: where Shopify puts its order). No counterparty.
TX_KEYS = ("transaction_id", "paypal_reference_id", "paypal_reference_id_type", "transaction_event_code",
           "transaction_initiation_date", "transaction_updated_date", "transaction_amount", "fee_amount",
           "transaction_status", "invoice_id", "custom_field", "ending_balance", "available_balance",
           "instrument_type", "instrument_sub_type", "bank_reference_id")
BALANCE_KEYS = ("currency", "primary", "total_balance", "available_balance", "withheld_balance")


class PayPalError(Exception):
    """A call that did not succeed. Its text is safe to show."""


class PayPalAuthFailed(PayPalError):
    """The Client ID / Secret was refused, or Transaction Search is not enabled. Latches the connection."""


class PayPalRequestRefused(PayPalError):
    """PayPal answered and refused this request (not a login or transport problem), e.g. a window with
    more results than it returns at once (RESULTSET_TOO_LARGE)."""


class PayPalRefused(PayPalError):
    """Refused before any request: not a PayPal host, no Client ID or no Secret, or not a read."""


@dataclass(frozen=True)
class TransactionPage:
    transactions: list[dict[str, Any]]
    page: int
    total_pages: int
    refreshed_at: str | None   # PayPal has everything up to this time


class PayPalGateway(Protocol):
    async def test(self, conn: Any) -> dict[str, Any]: ...
    async def transactions(self, conn: Any, first: datetime, last: datetime, page: int) -> TransactionPage: ...
    async def balances(self, conn: Any) -> list[dict[str, Any]]: ...


READS = ("test", "transactions", "balances")
WRITES: tuple[str, ...] = ()


def guarded(switch: str) -> dict[str, str]:
    return {m: switch for m in WRITES}  # nothing to guard: it never writes


def slim_transaction(t: dict[str, Any]) -> dict[str, Any]:
    info = t.get("transaction_info") if isinstance(t.get("transaction_info"), dict) else t
    return {k: info[k] for k in TX_KEYS if k in info}


def slim_balance(b: dict[str, Any]) -> dict[str, Any]:
    return {k: b[k] for k in BALANCE_KEYS if k in b}


def _iso(t: datetime) -> str:
    """An instant as PayPal wants it: ISO 8601 with an offset (a naive time is taken as UTC)."""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HttpPayPal:
    def __init__(self, *, secret: Callable[[str], str | None], transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.secret = secret
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        self._tokens: dict[tuple[str, str], tuple[str, float]] = {}  # (host, client id) → (token, valid until)

    @staticmethod
    def _host(conn: Any) -> str:
        host = (getattr(conn, "host", None) or "").strip().lower()
        if host not in HOSTS:
            raise PayPalRefused("the host must be api-m.paypal.com (or api-m.sandbox.paypal.com)")
        return host

    def _credentials(self, conn: Any) -> tuple[str, str]:
        client_id = (getattr(conn, "username", None) or "").strip()
        name = getattr(conn, "secret_name", None) or ""
        secret = self.secret(name) if name else None
        if not client_id:
            raise PayPalRefused("no Client ID: put it in the connection's user name")
        if not secret:
            raise PayPalRefused(f"no Secret: set {name or '<secret_name>'} in the server environment")
        return client_id, secret

    async def _send(self, c: httpx.AsyncClient, method: str, url: str, **kw: Any) -> httpx.Response:
        r: httpx.Response | None = None
        for attempt in range(RETRIES + 1):
            try:
                r = await c.request(method, url, **kw)
            except httpx.TimeoutException as e:
                if attempt == RETRIES:
                    raise PayPalError(readable(e)) from e
                await self.sleep(2.0 * (attempt + 1))
                continue
            except httpx.HTTPError as e:
                raise PayPalError(readable(e)) from e
            if (r.status_code < 500 and r.status_code != 429) or attempt == RETRIES:
                break
            await self.sleep(2.0 * (attempt + 1))
        assert r is not None
        return r

    async def _token(self, c: httpx.AsyncClient, conn: Any, host: str) -> str:
        client_id, secret = self._credentials(conn)
        kept = self._tokens.get((host, client_id))
        if kept and kept[1] > self.clock():
            return kept[0]
        r = await self._send(c, "POST", f"https://{host}/v1/oauth2/token", auth=(client_id, secret),
                             data={"grant_type": "client_credentials"}, headers={"Accept": "application/json"})
        if r.status_code in (400, 401):
            raise PayPalAuthFailed(f"PayPal refused the Client ID / Secret (HTTP {r.status_code})")
        if r.status_code != 200:
            raise PayPalError(f"PayPal answered HTTP {r.status_code} to the sign-in")
        try:
            data = r.json()
            token, ttl = str(data["access_token"]), int(data.get("expires_in") or 0)
        except (ValueError, KeyError, TypeError) as e:
            raise PayPalError("PayPal's sign-in answer had no token") from e
        self._tokens[(host, client_id)] = (token, self.clock() + max(ttl - TOKEN_SLACK, 0))
        return token

    async def _get(self, conn: Any, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if ("GET", path) not in REQUESTS:  # never anything that could pay, refund or change the account
            raise PayPalRefused(f"GET {path} is not a reporting request")
        host = self._host(conn)
        async with httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT) as c:
            token = await self._token(c, conn, host)
            r = await self._send(c, "GET", f"https://{host}{path}", params=params,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            if r.status_code == 401:  # the token expired early: once more with a new one
                self._tokens.pop((host, self._credentials(conn)[0]), None)
                token = await self._token(c, conn, host)
                r = await self._send(c, "GET", f"https://{host}{path}", params=params,
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 200 and isinstance(data, dict):
            return data
        name = str(data.get("name") or "") if isinstance(data, dict) else ""
        text = str(data.get("message") or "no detail")[:300] if isinstance(data, dict) else "no detail"
        if r.status_code == 401:
            raise PayPalAuthFailed(f"PayPal refused the token ({name or 'HTTP 401'}: {text})")
        if r.status_code == 403:
            raise PayPalAuthFailed(f"PayPal refused the reporting call ({name or 'HTTP 403'}: {text}). Turn on "
                                   "Transaction Search for the REST app (developer.paypal.com → Apps & Credentials → "
                                   "the app → Features).")
        if 400 <= r.status_code < 500:
            raise PayPalRequestRefused(f"PayPal refused GET {path} ({name or f'HTTP {r.status_code}'}: {text})")
        raise PayPalError(f"PayPal answered HTTP {r.status_code}")

    async def test(self, conn: Any) -> dict[str, Any]:
        data = await self._get(conn, "/v1/reporting/balances", {})
        return {"host": self._host(conn), "login": "accepted", "currencies": [b.get("currency") for b in data.get("balances") or []]}

    async def transactions(self, conn: Any, first: datetime, last: datetime, page: int) -> TransactionPage:
        if (last - first).days > MAX_DAYS:
            raise PayPalRefused(f"a transaction search covers at most {MAX_DAYS} days")
        data = await self._get(conn, "/v1/reporting/transactions", {
            "start_date": _iso(first), "end_date": _iso(last), "fields": "transaction_info",
            "page_size": PAGE_SIZE, "page": page})
        txs = [slim_transaction(t) for t in data.get("transaction_details") or [] if isinstance(t, dict)]
        return TransactionPage(txs, int(data.get("page") or page), int(data.get("total_pages") or 1),
                               data.get("last_refreshed_datetime"))

    async def balances(self, conn: Any) -> list[dict[str, Any]]:
        data = await self._get(conn, "/v1/reporting/balances", {})
        return [slim_balance(b) for b in data.get("balances") or [] if isinstance(b, dict)]


def readable(e: BaseException) -> str:
    if isinstance(e, PayPalError):
        return str(e)
    if isinstance(e, httpx.TimeoutException):
        return "PayPal did not answer in time."
    if isinstance(e, httpx.ConnectError):
        return "PayPal could not be reached."
    return f"The PayPal request failed ({type(e).__name__})."


def summary(method: str, args: tuple, kwargs: dict) -> dict[str, Any]:
    conn = args[0] if args else kwargs.get("conn")
    return {"connection": getattr(conn, "id", None)}


@dataclass
class FakePayPal:
    """In memory for tests: the account's transactions (each a full Transaction Search item, payer and
    all) and its balances. Returns what the real client returns (slimmed), and records every call."""
    items: list[dict[str, Any]] = field(default_factory=list)
    balance: list[dict[str, Any]] = field(default_factory=list)
    calls: list[tuple[str, Any]] = field(default_factory=list)
    fail: dict[str, BaseException] = field(default_factory=dict)
    page_size: int = PAGE_SIZE
    refreshed_at: str | None = None

    def _maybe_fail(self, method: str) -> None:
        if method in self.fail:
            raise self.fail[method]

    async def test(self, conn: Any) -> dict[str, Any]:
        self.calls.append(("test", None))
        self._maybe_fail("test")
        return {"host": getattr(conn, "host", None), "login": "accepted", "currencies": [b.get("currency") for b in self.balance]}

    async def transactions(self, conn: Any, first: datetime, last: datetime, page: int) -> TransactionPage:
        self.calls.append(("transactions", (first, last, page)))
        self._maybe_fail("transactions")
        assert (last - first).days <= MAX_DAYS
        inside = []
        for t in self.items:
            info = t.get("transaction_info", t)
            at = datetime.fromisoformat(str(info["transaction_initiation_date"]).replace("Z", "+00:00"))
            if first <= at <= last:
                inside.append(t)
        pages = max(1, -(-len(inside) // self.page_size))
        part = inside[(page - 1) * self.page_size: page * self.page_size]
        return TransactionPage([slim_transaction(t) for t in part], page, pages, self.refreshed_at)

    async def balances(self, conn: Any) -> list[dict[str, Any]]:
        self.calls.append(("balances", None))
        self._maybe_fail("balances")
        return [slim_balance(b) for b in self.balance]
