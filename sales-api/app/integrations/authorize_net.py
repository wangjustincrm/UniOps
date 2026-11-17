"""Authorize.Net integration (Finance, user 2026-11-17): the Transaction Reporting API, read-only.

Three reads, all POSTs to `https://<host>/xml/v1/request.api` with a JSON body:

- `getSettledBatchListRequest` — the settled batches between two dates (≤ 31 days per call),
  with their per-card-type statistics: each batch is one deposit to the bank.
- `getTransactionListRequest` — the transactions of one batch, 1,000 per page.
- `getTransactionDetailsRequest` — one transaction's order fields (invoice number,
  description), read only for a transaction the list could not match to Shopify.

No method writes: the same login (API Login ID + Transaction Key) could charge a card, but this
client only ever sends the three request names above (`REQUESTS`) and refuses any other.

Credentials: the connection's `username` is the API Login ID (not a secret); the Transaction Key
is read from the server environment under the connection's `secret_name`. Neither appears in a
message or a log. The host must be `api.authorize.net` (or the sandbox `apitest.authorize.net`).

No card or personal data is kept: every transaction is reduced to `TX_KEYS` / `DETAIL_KEYS`
(`slim_transaction`, `slim_details`) before anything sees it — the masked card number, the
holder's name, the billing / shipping address, the customer's e-mail and IP never leave here.
The card type (Visa, MasterCard, …) is kept: it is what the batch statistics are split by.

Authorize.Net puts a byte-order mark before its JSON; it is removed before parsing.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

HOSTS = ("api.authorize.net", "apitest.authorize.net")
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
MAX_DAYS = 31          # the batch list's widest window
PAGE_LIMIT = 1000      # the transaction list's largest page
RETRIES = 3            # a 5xx / timeout is tried again, with a pause
REQUESTS = ("authenticateTestRequest", "getSettledBatchListRequest", "getTransactionListRequest",
            "getTransactionDetailsRequest")

# What is kept of a transaction in a batch list (no card number, no names).
TX_KEYS = ("transId", "submitTimeUTC", "transactionStatus", "invoiceNumber", "accountType", "settleAmount",
           "marketType", "product", "hasReturnedItems")
# What is kept of a transaction's details: its type, amounts, batch and order fields.
DETAIL_KEYS = ("transId", "refTransId", "splitTenderId", "submitTimeUTC", "transactionType", "transactionStatus",
               "responseCode", "authAmount", "settleAmount", "marketType", "product", "batch", "order",
               "recurringBilling", "returnedItems", "solution")
ORDER_KEYS = ("invoiceNumber", "description", "purchaseOrderNumber")
BATCH_KEYS = ("batchId", "settlementTimeUTC", "settlementState", "paymentMethod", "marketType", "product")

# The Transaction Details API's own refusals: a bad login, and the API not turned on for the account.
AUTH_CODES = frozenset({"E00007", "E00008", "E00124"})
NOT_ENABLED_CODES = frozenset({"E00011"})


class AuthorizeNetError(Exception):
    """A call that did not succeed. Its text is safe to show."""


class AuthorizeNetAuthFailed(AuthorizeNetError):
    """The login or key was refused, or the reporting API is off. Latches the connection."""


class AuthorizeNetRefused(AuthorizeNetError):
    """Refused before any request: not an Authorize.Net host, no login id or no key."""


@dataclass(frozen=True)
class TransactionPage:
    transactions: list[dict[str, Any]]
    total: int


class AuthorizeNetGateway(Protocol):
    async def test(self, conn: Any) -> dict[str, Any]: ...
    async def settled_batches(self, conn: Any, first: datetime, last: datetime) -> list[dict[str, Any]]: ...
    async def batch_transactions(self, conn: Any, batch_id: str, page: int) -> TransactionPage: ...
    async def transaction_details(self, conn: Any, trans_id: str) -> dict[str, Any]: ...


READS = ("test", "settled_batches", "batch_transactions", "transaction_details")
WRITES: tuple[str, ...] = ()


def guarded(switch: str) -> dict[str, str]:
    return {m: switch for m in WRITES}  # nothing to guard: it never writes


def slim_transaction(t: dict[str, Any]) -> dict[str, Any]:
    return {k: t[k] for k in TX_KEYS if k in t}


def slim_details(t: dict[str, Any]) -> dict[str, Any]:
    out = {k: t[k] for k in DETAIL_KEYS if k in t}
    if isinstance(out.get("order"), dict):
        out["order"] = {k: out["order"][k] for k in ORDER_KEYS if k in out["order"]}
    if isinstance(out.get("batch"), dict):
        out["batch"] = {k: out["batch"][k] for k in BATCH_KEYS if k in out["batch"]}
    return out


def slim_batch(b: dict[str, Any]) -> dict[str, Any]:
    out = {k: b[k] for k in BATCH_KEYS if k in b}
    if isinstance(b.get("statistics"), list):
        out["statistics"] = [s for s in b["statistics"] if isinstance(s, dict)]
    return out


def _utc(t: datetime) -> str:
    """A UTC instant as the API wants it (an aware time is converted; a naive one is taken as UTC)."""
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class HttpAuthorizeNet:
    def __init__(self, *, secret: Callable[[str], str | None], transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        self.secret = secret
        self.transport = transport
        self.sleep = sleep

    @staticmethod
    def _host(conn: Any) -> str:
        host = (getattr(conn, "host", None) or "").strip().lower()
        if host not in HOSTS:
            raise AuthorizeNetRefused("the host must be api.authorize.net (or apitest.authorize.net)")
        return host

    def _auth(self, conn: Any) -> dict[str, str]:
        login = (getattr(conn, "username", None) or "").strip()
        name = getattr(conn, "secret_name", None) or ""
        key = self.secret(name) if name else None
        if not login:
            raise AuthorizeNetRefused("no API Login ID: put it in the connection's user name")
        if not key:
            raise AuthorizeNetRefused(f"no Transaction Key: set {name or '<secret_name>'} in the server environment")
        return {"name": login, "transactionKey": key}

    async def _call(self, conn: Any, request: str, body: dict[str, Any]) -> dict[str, Any]:
        if request not in REQUESTS:  # never anything that could charge, refund or void
            raise AuthorizeNetRefused(f"{request} is not a reporting request")
        host = self._host(conn)
        payload = {request: {"merchantAuthentication": self._auth(conn), **body}}
        r: httpx.Response | None = None
        async with httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT) as c:
            for attempt in range(RETRIES + 1):
                try:
                    r = await c.post(f"https://{host}/xml/v1/request.api", json=payload,
                                     headers={"Content-Type": "application/json"})
                except httpx.TimeoutException as e:
                    if attempt == RETRIES:
                        raise AuthorizeNetError(readable(e)) from e
                    await self.sleep(2.0 * (attempt + 1))
                    continue
                except httpx.HTTPError as e:
                    raise AuthorizeNetError(readable(e)) from e
                if r.status_code < 500 or attempt == RETRIES:
                    break
                await self.sleep(2.0 * (attempt + 1))
        assert r is not None
        if r.status_code != 200:
            raise AuthorizeNetError(f"Authorize.Net answered HTTP {r.status_code}")
        try:
            data = json.loads(r.content.decode("utf-8-sig"))  # a byte-order mark comes first
        except ValueError as e:
            raise AuthorizeNetError("Authorize.Net answered something that is not JSON") from e
        messages = data.get("messages") or {}
        if messages.get("resultCode") != "Ok":
            first = (messages.get("message") or [{}])[0]
            code, text = str(first.get("code") or ""), str(first.get("text") or "no detail")[:300]
            if code in AUTH_CODES:
                raise AuthorizeNetAuthFailed(f"Authorize.Net refused the login ({code}: {text})")
            if code in NOT_ENABLED_CODES:
                raise AuthorizeNetAuthFailed(f"the Transaction Details API is not turned on for this account ({code}: {text}). "
                                             "Turn it on under Account → Security Settings → Transaction Details API.")
            if code == "E00040" and request == "getTransactionDetailsRequest":
                return {}  # the record cannot be found: nothing to add
            raise AuthorizeNetError(f"Authorize.Net refused {request} ({code}: {text})")
        return data

    async def test(self, conn: Any) -> dict[str, Any]:
        await self._call(conn, "authenticateTestRequest", {})
        return {"host": self._host(conn), "login": "accepted"}

    async def settled_batches(self, conn: Any, first: datetime, last: datetime) -> list[dict[str, Any]]:
        if (last - first).days > MAX_DAYS:
            raise AuthorizeNetRefused(f"a batch list covers at most {MAX_DAYS} days")
        data = await self._call(conn, "getSettledBatchListRequest", {
            "includeStatistics": "true",
            "firstSettlementDate": _utc(first), "lastSettlementDate": _utc(last)})
        return [slim_batch(b) for b in data.get("batchList") or []]

    async def batch_transactions(self, conn: Any, batch_id: str, page: int) -> TransactionPage:
        data = await self._call(conn, "getTransactionListRequest", {
            "batchId": str(batch_id),
            "sorting": {"orderBy": "submitTimeUTC", "orderDescending": "false"},
            "paging": {"limit": str(PAGE_LIMIT), "offset": str(page)}})
        txs = [slim_transaction(t) for t in data.get("transactions") or []]
        return TransactionPage(txs, int(data.get("totalNumInResultSet") or len(txs)))

    async def transaction_details(self, conn: Any, trans_id: str) -> dict[str, Any]:
        data = await self._call(conn, "getTransactionDetailsRequest", {"transId": str(trans_id)})
        t = data.get("transaction")
        return slim_details(t) if isinstance(t, dict) else {}


def readable(e: BaseException) -> str:
    if isinstance(e, AuthorizeNetError):
        return str(e)
    if isinstance(e, httpx.TimeoutException):
        return "Authorize.Net did not answer in time."
    if isinstance(e, httpx.ConnectError):
        return "Authorize.Net could not be reached."
    return f"The Authorize.Net request failed ({type(e).__name__})."


def summary(method: str, args: tuple, kwargs: dict) -> dict[str, Any]:
    conn = args[0] if args else kwargs.get("conn")
    return {"connection": getattr(conn, "id", None)}


@dataclass
class FakeAuthorizeNet:
    """In memory for tests: batches with their transactions; details per id. Returns what the real
    client returns (slimmed), and records every call."""
    batches: list[dict[str, Any]] = field(default_factory=list)          # each with "transactions": [...]
    details: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)
    fail: dict[str, BaseException] = field(default_factory=dict)
    page_limit: int = PAGE_LIMIT

    def _maybe_fail(self, method: str) -> None:
        if method in self.fail:
            raise self.fail[method]

    async def test(self, conn: Any) -> dict[str, Any]:
        self.calls.append(("test", None))
        self._maybe_fail("test")
        return {"host": getattr(conn, "host", None), "login": "accepted"}

    async def settled_batches(self, conn: Any, first: datetime, last: datetime) -> list[dict[str, Any]]:
        self.calls.append(("settled_batches", (first, last)))
        self._maybe_fail("settled_batches")
        assert (last - first).days <= MAX_DAYS
        out = []
        for b in self.batches:
            at = datetime.fromisoformat(b["settlementTimeUTC"].replace("Z", "+00:00"))
            if first <= at <= last:
                out.append(slim_batch(b))
        return out

    async def batch_transactions(self, conn: Any, batch_id: str, page: int) -> TransactionPage:
        self.calls.append(("batch_transactions", (batch_id, page)))
        self._maybe_fail("batch_transactions")
        txs = next((b.get("transactions", []) for b in self.batches if b["batchId"] == batch_id), [])
        part = txs[(page - 1) * self.page_limit: page * self.page_limit]
        return TransactionPage([slim_transaction(t) for t in part], len(txs))

    async def transaction_details(self, conn: Any, trans_id: str) -> dict[str, Any]:
        self.calls.append(("transaction_details", trans_id))
        self._maybe_fail("transaction_details")
        t = self.details.get(str(trans_id))
        return slim_details(t) if t else {}
