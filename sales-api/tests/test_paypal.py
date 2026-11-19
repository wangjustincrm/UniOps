"""PayPal (Finance, user 2026-11-19): the reporting client — reads only, a token kept until it expires and
never shown, no payer data kept, PayPal's refusals read."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.domain.connections import check_connection
from app.domain.gateways import paypal_kind, paypal_settled
from app.integrations.paypal import (
    MAX_DAYS, FakePayPal, HttpPayPal, PayPalAuthFailed, PayPalError, PayPalRefused, PayPalRequestRefused, slim_transaction,
)
from app.models.config import Connection
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import InvalidInput
from app.platform.query import QuerySpec, parse_filter
from app.services.intake import ConnectionSnapshot
from app.services.payment_list import PaymentRowRepository
from app.services.paypal_intake import PayPalIntake
from tests.test_orders import OP, env, shopify_order  # noqa: F401 — env is a fixture

SECRET = "s3cretPayPalSecret"
CONN = SimpleNamespace(id=2, host="api-m.paypal.com", username="client-id-123", secret_name="SALES_SECRET_PAYPAL")
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)

ITEM = {  # one Transaction Search item, as PayPal sends it (payer and shipping included)
    "transaction_info": {
        "paypal_account_id": "PAYERACCT1", "transaction_id": "5D158248WR160752Y", "transaction_event_code": "T0006",
        "transaction_initiation_date": "2026-10-02T14:00:00+0000", "transaction_updated_date": "2026-10-02T14:00:05+0000",
        "transaction_amount": {"currency_code": "CAD", "value": "120.00"}, "fee_amount": {"currency_code": "CAD", "value": "-3.78"},
        "transaction_status": "S", "transaction_subject": "Order from Jane", "transaction_note": "please hurry, Jane",
        "invoice_id": "SHP-9301", "custom_field": "9301", "ending_balance": {"currency_code": "CAD", "value": "500.00"},
        "protection_eligibility": "01"},
    "payer_info": {"account_id": "PAYERACCT1", "email_address": "jane@example.com", "payer_name": {"given_name": "Jane"},
                   "address": {"line1": "1 Main St"}, "phone_number": {"national_number": "4165550000"}},
    "shipping_info": {"name": "Jane Doe", "address": {"line1": "1 Main St"}},
    "cart_info": {"item_details": [{"item_name": "Milk"}]},
}


async def _nothing():
    return None


def client(handler, *, secret=None, clock=None):
    kw = {"clock": clock} if clock else {}
    return HttpPayPal(secret=secret or {"SALES_SECRET_PAYPAL": SECRET}.get, transport=httpx.MockTransport(handler),
                      sleep=lambda s: _nothing(), **kw)


def token(n=1, ttl=32400):
    return httpx.Response(200, json={"access_token": f"tok{n}", "token_type": "Bearer", "expires_in": ttl})


async def test_the_client_signs_in_once_reads_pages_and_keeps_no_payer_data():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, dict(req.url.params), req.headers.get("authorization", "")))
        if req.url.path == "/v1/oauth2/token":
            assert req.headers["authorization"].startswith("Basic ")  # client id and secret, never in the URL
            assert b"grant_type=client_credentials" in req.content
            return token()
        assert req.headers["authorization"] == "Bearer tok1"
        if req.url.path == "/v1/reporting/transactions":
            return httpx.Response(200, json={"transaction_details": [ITEM], "page": 1, "total_pages": 2,
                                             "last_refreshed_datetime": "2026-10-31T10:00:00+0000"})
        return httpx.Response(200, json={"balances": [{"currency": "CAD", "primary": True, "total_balance": {"value": "500.00"},
                                                       "account_holder": "Someone"}]})

    c = client(handler)
    page = await c.transactions(CONN, T0, T0 + timedelta(days=31), 1)
    assert (page.page, page.total_pages, page.refreshed_at) == (1, 2, "2026-10-31T10:00:00+0000")
    [t] = page.transactions
    assert t["transaction_id"] == "5D158248WR160752Y" and t["fee_amount"]["value"] == "-3.78" and t["invoice_id"] == "SHP-9301"
    kept = repr(t)
    for personal in ("jane", "Jane", "PAYERACCT1", "Main St", "4165550000", "hurry", "Milk"):
        assert personal not in kept
    assert await c.balances(CONN) == [{"currency": "CAD", "primary": True, "total_balance": {"value": "500.00"}}]
    assert [s[:2] for s in seen] == [("POST", "/v1/oauth2/token"), ("GET", "/v1/reporting/transactions"),
                                      ("GET", "/v1/reporting/balances")]  # one sign-in for both reads
    q = seen[1][2]
    assert (q["start_date"], q["end_date"], q["page_size"], q["page"]) == ("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", "500", "1")
    assert q["fields"] == "transaction_info"  # the payer's data is never even asked for (review 2026-11-19)


async def test_the_token_is_renewed_once_it_expires_and_never_appears_in_an_error():
    now = [0.0]
    signins = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/oauth2/token":
            signins.append(1)
            return token(len(signins), ttl=120)
        if req.headers["authorization"] == "Bearer tok1" and now[0] > 100:
            raise AssertionError("an expired token was sent")
        return httpx.Response(200, json={"balances": []})

    c = client(handler, clock=lambda: now[0])
    await c.balances(CONN)
    await c.balances(CONN)
    assert len(signins) == 1
    now[0] = 61.0  # 120 s less the minute of slack
    await c.balances(CONN)
    assert len(signins) == 2

    def refuses(req):
        if req.url.path == "/v1/oauth2/token":
            return token(9)
        return httpx.Response(403, json={"name": "NOT_AUTHORIZED", "message": "Authorization failed due to insufficient permissions."})
    with pytest.raises(PayPalAuthFailed) as e:
        await client(refuses).transactions(CONN, T0, T0 + timedelta(days=1), 1)
    assert "Transaction Search" in str(e.value) and "tok9" not in str(e.value) and SECRET not in str(e.value)


async def test_the_client_refuses_before_sending_and_reads_paypal_s_refusals():
    def never(req):
        raise AssertionError("nothing may be sent")

    for conn, why in ((SimpleNamespace(**{**vars(CONN), "host": "api.paypal.com"}), "host"),
                      (SimpleNamespace(**{**vars(CONN), "username": ""}), "Client ID"),
                      (SimpleNamespace(**{**vars(CONN), "secret_name": "MISSING"}), "MISSING")):
        with pytest.raises(PayPalRefused, match=why):
            await client(never).balances(conn)
    with pytest.raises(PayPalRefused, match="31 days"):
        await client(never).transactions(CONN, T0, T0 + timedelta(days=MAX_DAYS + 1), 1)
    with pytest.raises(PayPalRefused, match="not a reporting request"):
        await client(never)._get(CONN, "/v2/payments/captures/X/refund", {})

    def bad_login(req):
        return httpx.Response(401, json={"error": "invalid_client", "error_description": "Client Authentication failed"})
    with pytest.raises(PayPalAuthFailed, match="Client ID / Secret"):
        await client(bad_login).balances(CONN)

    def too_large(req):
        if req.url.path == "/v1/oauth2/token":
            return token()
        return httpx.Response(400, json={"name": "RESULTSET_TOO_LARGE", "message": "Result set size is greater than the maximum limit."})
    with pytest.raises(PayPalRequestRefused, match="RESULTSET_TOO_LARGE"):
        await client(too_large).transactions(CONN, T0, T0 + timedelta(days=1), 1)

    calls = []

    def down(req):
        calls.append(1)
        if req.url.path == "/v1/oauth2/token":
            return token()
        return httpx.Response(503)
    with pytest.raises(PayPalError, match="HTTP 503"):
        await client(down).balances(CONN)
    assert len(calls) == 1 + 4  # the sign-in, then the read tried RETRIES more times


async def test_the_fake_returns_what_the_real_client_returns():
    fake = FakePayPal(items=[ITEM, {**ITEM, "transaction_info": {**ITEM["transaction_info"], "transaction_id": "OUTSIDE",
                                                                  "transaction_initiation_date": "2026-12-01T00:00:00+0000"}}])
    page = await fake.transactions(CONN, T0, T0 + timedelta(days=31), 1)
    assert page.transactions == [slim_transaction(ITEM)] and page.total_pages == 1


# ── the rules ────────────────────────────────────────────────────────────────

def test_event_codes_say_sale_refund_or_other_and_status_whether_money_moved():
    assert [paypal_kind(c, v) for c, v in (("T0006", "120.00"), ("T0003", "5.00"), ("T0006", "-120.00"), ("T1107", "-30.00"),
                                           ("T1106", "-30.00"), ("T0403", "-500.00"), ("T0200", "-50.00"), ("T0200", "37.00"),
                                           ("T0100", "-1.00"), ("T2101", "-10.00"), (None, "1"))] == [
        "sale", "sale", "other", "refund", "other", "other", "other", "other", "other", "other", "other"]
    assert [paypal_settled(st, k) for st, k in (("S", "sale"), ("F", "sale"), ("P", "sale"), ("V", "sale"), ("D", "refund"),
                                               ("S", "refund"), ("S", "other"))] == [True, True, False, False, False, True, False]


def test_a_paypal_connection_reads_only_with_its_client_id_and_secret_name():
    ok = {"name": "paypal", "kind": "PAYPAL", "host": "api-m.paypal.com", "username": "client-id", "secret_name": "SALES_SECRET_PAYPAL",
          "after_read": "LEAVE"}
    check_connection(ok)
    check_connection({**ok, "host": "api-m.sandbox.paypal.com"})
    for bad, code in (({"host": "api.paypal.com"}, "connection.paypal_host"), ({"username": ""}, "connection.field_required"),
                      ({"can_write": True}, "connection.read_only_kind"), ({"inbound_folder": "/in"}, "connection.not_for_kind")):
        with pytest.raises(InvalidInput) as e:
            check_connection({**ok, **bad})
        assert e.value.code == code


# ── one pass, end to end ─────────────────────────────────────────────────────

def item(tid, code, value, at, *, fee=None, status="S", invoice=None, custom=None, ref=None, currency="CAD"):
    info = {"transaction_id": tid, "transaction_event_code": code, "transaction_initiation_date": at,
            "transaction_amount": {"currency_code": currency, "value": value}, "transaction_status": status,
            "invoice_id": invoice, "custom_field": custom, "paypal_reference_id": ref}
    if fee is not None:
        info["fee_amount"] = {"currency_code": currency, "value": fee}
    return {"transaction_info": info, "payer_info": {"email_address": "payer@example.com", "payer_name": {"given_name": "Pat"}}}


async def paypal_connection(env, cursor, name="paypal"):  # noqa: F811
    async with env.factory(SYSTEM_ACTOR) as u:
        c = Connection(name=name, kind="PAYPAL", host="api-m.paypal.com", username="client-id", secret_name="SALES_SECRET_PAYPAL",
                       after_read="LEAVE", can_write=False, cursor_updated_at=cursor, schedule_minutes=60, version=1)
        u.session.add(c)
        await u.session.flush()
        await u.commit()
        return c.id


async def snap(env, cid):  # noqa: F811
    async with env.factory(SYSTEM_ACTOR) as u:
        return ConnectionSnapshot.of(await u.session.get(Connection, cid))


async def test_a_pass_reads_transactions_with_fees_matches_by_paypal_s_id_and_feeds_the_payments_list(env):  # noqa: F811
    tx = lambda id, **kw: {"id": id, "kind": "sale", "status": "success", "currency": "CAD", "test": False, "gateway": "paypal",  # noqa: E731
                           "processed_at": "2026-10-02T10:00:00-04:00", **kw}
    env.shop.orders = [shopify_order(1, 9301), shopify_order(2, 9302), shopify_order(3, 9303)]
    env.shop.transactions_by_order |= {
        "1": [tx(101, amount="120.00", authorization="5D158248WR160752Y"),
              tx(102, kind="refund", amount="30.00", authorization="7RF00000AA000001X", processed_at="2026-10-05T10:00:00-04:00")],
        "2": [tx(201, amount="50.00", authorization="9XX00000BB000002Z")],
        # 3: paid in Shopify, not in PayPal although its data covers the date
        "3": [tx(301, amount="80.00", authorization="1NOTINPAYPAL00003", processed_at="2026-10-03T10:00:00-04:00")],
    }
    await env.pull()
    fake = FakePayPal(page_size=2, refreshed_at="2026-10-20T00:00:00+0000")
    fake.items = [
        item("5D158248WR160752Y", "T0006", "120.00", "2026-10-02T14:00:00+0000", fee="-3.78", invoice="SHP-9301"),
        item("7RF00000AA000001X", "T1107", "-30.00", "2026-10-05T14:00:00+0000", fee="0.90", ref="5D158248WR160752Y"),
        item("9XX00000BB000002Z", "T0006", "50.00", "2026-10-02T15:00:00+0000", fee="-1.75"),
        item("4TYPEDINPAYPAL004", "T0006", "25.00", "2026-10-06T15:00:00+0000", fee="-1.03"),  # not a Shopify payment
        item("8WITHDRAW0000005", "T0403", "-150.00", "2026-10-10T15:00:00+0000"),           # to the bank
        item("2CONVERT00000006", "T0200", "-10.00", "2026-10-11T15:00:00+0000"),            # CAD → USD
        item("3PENDING00000007", "T0006", "60.00", "2026-10-12T15:00:00+0000", status="P"),
    ]
    cid = await paypal_connection(env, datetime(2026, 10, 1, tzinfo=timezone.utc))
    clock = lambda: datetime(2026, 10, 20, 6, tzinfo=timezone.utc)  # noqa: E731
    r = await PayPalIntake(uow_factory=env.factory, gateway=fake, clock=clock).run_pass(await snap(env, cid))
    assert (r.status, r.transactions, r.matched) == ("ok", 7, 3)
    assert r.cursor_after == "2026-10-20T00:00:00+00:00"  # PayPal's own "has everything up to", not the clock

    rows = {x["external_id"]: x for x in await env.q(
        "SELECT external_id, kind, settled, amount, fee_amount, match_method, payment_row_id, raw::text AS raw FROM sales_gateway_transactions")}
    assert {k: (v["kind"], v["settled"]) for k, v in rows.items()} == {
        "5D158248WR160752Y": ("sale", True), "7RF00000AA000001X": ("refund", True), "9XX00000BB000002Z": ("sale", True),
        "4TYPEDINPAYPAL004": ("sale", True), "8WITHDRAW0000005": ("other", False), "2CONVERT00000006": ("other", False),
        "3PENDING00000007": ("sale", False)}
    assert (rows["5D158248WR160752Y"]["fee_amount"], rows["7RF00000AA000001X"]["fee_amount"]) == (Decimal("3.78"), Decimal("-0.90"))
    assert {k for k, v in rows.items() if v["match_method"] == "TRANSACTION_ID"} == {
        "5D158248WR160752Y", "7RF00000AA000001X", "9XX00000BB000002Z"}
    assert not any("payer" in v["raw"] or "Pat" in v["raw"] for v in rows.values())  # no payer kept
    docs = await env.q("SELECT count(*) AS n FROM sales_documents WHERE system = 'PAYPAL' AND kind = 'GATEWAY_JSON'")
    assert docs[0]["n"] >= 4  # pages of 2

    async with env.factory(OP) as u:
        page = await PaymentRowRepository(u).list(QuerySpec(filters=[parse_filter("method:eq:PayPal")], page_size=50))
    by_ref = {x.gateway_ref: x for x in page.items}
    sale = by_ref["5D158248WR160752Y"]
    assert (sale.gateway_fee, sale.net_after_fee, sale.match_method) == (Decimal("3.78"), Decimal("116.22"), "Transaction ID")
    assert by_ref["7RF00000AA000001X"].net_after_fee == Decimal("-29.10")  # the fee given back with the refund
    assert by_ref["4TYPEDINPAYPAL004"].issue == "In the gateway, not in Shopify"
    assert by_ref["1NOTINPAYPAL00003"].issue == "Not settled in the gateway"
    assert "8WITHDRAW0000005" not in by_ref and "2CONVERT00000006" not in by_ref  # neither a sale nor a refund


async def test_a_refused_secret_latches_and_a_window_too_large_is_read_in_halves(env):  # noqa: F811
    fake = FakePayPal(fail={"transactions": PayPalAuthFailed("PayPal refused the Client ID / Secret (HTTP 401)")})
    cid = await paypal_connection(env, datetime(2026, 10, 1, tzinfo=timezone.utc))
    clock = lambda: datetime(2026, 10, 20, tzinfo=timezone.utc)  # noqa: E731
    r = await PayPalIntake(uow_factory=env.factory, gateway=fake, clock=clock).run_pass(await snap(env, cid))
    assert r.status == "latched" and (await snap(env, cid)).auth_failed_at is not None

    class Halving(FakePayPal):
        async def transactions(self, conn, first, last, page):
            if last - first > timedelta(days=8):
                self.calls.append(("too_large", (first, last)))
                raise PayPalRequestRefused("PayPal refused GET /v1/reporting/transactions (RESULTSET_TOO_LARGE: too many)")
            return await super().transactions(conn, first, last, page)

    halving = Halving(items=[item("5ONE00000000000A", "T0006", "10.00", "2026-10-15T00:00:00+0000")])
    cid2 = await paypal_connection(env, datetime(2026, 10, 1, tzinfo=timezone.utc), name="paypal 2")
    r2 = await PayPalIntake(uow_factory=env.factory, gateway=halving, clock=clock).run_pass(await snap(env, cid2))
    assert r2.status == "ok" and r2.transactions == 1 and any(c[0] == "too_large" for c in halving.calls)
    assert r2.cursor_after == "2026-10-20T00:00:00+00:00"  # read to the end, in smaller windows
