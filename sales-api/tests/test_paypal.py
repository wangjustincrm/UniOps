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
        # one id, two events (a hold and its release, as on dev2 2026-11-19): two rows, neither a sale
        item("3FVHOLD000000008", "T1110", "-81.24", "2026-10-13T15:00:00+0000", status="P", ref="5D158248WR160752Y"),
        item("3FVHOLD000000008", "T1111", "81.24", "2026-10-13T15:04:00+0000", status="D", ref="5D158248WR160752Y"),
    ]
    cid = await paypal_connection(env, datetime(2026, 10, 1, tzinfo=timezone.utc))
    clock = lambda: datetime(2026, 10, 20, 6, tzinfo=timezone.utc)  # noqa: E731
    r = await PayPalIntake(uow_factory=env.factory, gateway=fake, clock=clock).run_pass(await snap(env, cid))
    assert (r.status, r.transactions, r.matched) == ("ok", 9, 3)
    assert r.cursor_after == "2026-10-20T00:00:00+00:00"  # PayPal's own "has everything up to", not the clock

    rows = {x["external_id"]: x for x in await env.q(
        "SELECT external_id, kind, settled, amount, fee_amount, match_method, payment_row_id, raw::text AS raw FROM sales_gateway_transactions")}
    assert {k: (v["kind"], v["settled"]) for k, v in rows.items()} == {
        "5D158248WR160752Y": ("sale", True), "7RF00000AA000001X": ("refund", True), "9XX00000BB000002Z": ("sale", True),
        "4TYPEDINPAYPAL004": ("sale", True), "8WITHDRAW0000005:T0403": ("other", False), "2CONVERT00000006:T0200": ("other", False),
        "3PENDING00000007": ("sale", False), "3FVHOLD000000008:T1110": ("other", False),
        "3FVHOLD000000008:T1111": ("other", False)}
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
    assert not any(k and ("8WITHDRAW" in k or "2CONVERT" in k or "3FVHOLD" in k) for k in by_ref)  # neither a sale nor a refund


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


async def test_each_transfer_to_the_bank_is_a_settlement_with_its_period_and_the_rest_is_in_the_balance(env):  # noqa: F811
    """Option A (user 2026-11-19): transfers T0400 close their period; a hold and its release and a deposit from the bank
    are in the notes; the transfer's difference from the period's net is the carried balance, not forced to zero."""
    from app.services.settlement_list import SettlementRowRepository, SettlementRowService
    tx = lambda id, **kw: {"id": id, "kind": "sale", "status": "success", "currency": "CAD", "test": False, "gateway": "paypal",  # noqa: E731
                           "processed_at": "2026-10-02T10:00:00-04:00", **kw}
    env.shop.orders = [shopify_order(1, 9301), shopify_order(2, 9302), shopify_order(3, 9303)]
    env.shop.transactions_by_order |= {
        "1": [tx(101, amount="100.00", authorization="1SALE00000000001"),
              tx(102, kind="refund", amount="5.00", authorization="5REFUNDPEND00005", processed_at="2026-10-06T06:00:00-04:00")],
        "2": [tx(201, amount="50.00", authorization="2SALE00000000002"),
              tx(202, kind="refund", amount="10.00", authorization="2REFUND000000002", processed_at="2026-10-04T10:00:00-04:00")],
        "3": [tx(301, amount="40.00", authorization="3SALE00000000003", processed_at="2026-10-12T10:00:00-04:00")],
    }
    await env.pull()
    fake = FakePayPal(refreshed_at="2026-10-20T00:00:00+0000")
    fake.items = [
        item("1SALE00000000001", "T0006", "100.00", "2026-10-02T14:00:00+0000", fee="-3.20"),
        item("2SALE00000000002", "T0006", "50.00", "2026-10-02T15:00:00+0000", fee="-1.75"),
        item("2REFUND000000002", "T1107", "-10.00", "2026-10-04T14:00:00+0000", fee="0.35", ref="2SALE00000000002"),
        item("9HOLD0000000000H", "T1110", "-20.00", "2026-10-05T14:00:00+0000", status="P"),
        item("9HOLD0000000000H", "T1111", "20.00", "2026-10-05T16:00:00+0000", status="D"),
        item("8DEPOSIT00000008", "T0300", "5.00", "2026-10-06T10:00:00+0000"),
        # a refund the balance could not pay: PayPal keeps it pending; the bank deposit that funded it names it
        item("5REFUNDPEND00005", "T1107", "-5.00", "2026-10-06T10:00:00+0000", status="P", ref="1SALE00000000001"),
        item("5DEPOSIT0000005B", "T0300", "5.00", "2026-10-06T10:00:00+0000", ref="5REFUNDPEND00005"),
        # first transfer: the period's net is 100 + 50 − 10 − 5 − (3.20 + 1.75 − 0.35) = 130.40; 130.00 went
        item("7TRANSFER0000001", "T0400", "-130.00", "2026-10-07T12:00:00+0000"),
        item("3SALE00000000003", "T0006", "40.00", "2026-10-12T14:00:00+0000", fee="-1.46"),
        # second transfer: net 38.54, the 0.40 left from before goes too
        item("7TRANSFER0000002", "T0400", "-38.94", "2026-10-13T12:00:00+0000"),
        item("4AFTER0000000004", "T0006", "12.00", "2026-10-15T14:00:00+0000", fee="-0.65"),  # still in the balance
    ]
    cid = await paypal_connection(env, datetime(2026, 10, 1, tzinfo=timezone.utc))
    clock = lambda: datetime(2026, 10, 20, 6, tzinfo=timezone.utc)  # noqa: E731
    intake = PayPalIntake(uow_factory=env.factory, gateway=fake, clock=clock)
    r = await intake.run_pass(await snap(env, cid))
    assert (r.status, r.batches, r.matched) == ("ok", 2, 5)  # the bank-funded refund too (user 2026-11-19)

    async with env.factory(OP) as u:
        page = await SettlementRowRepository(u).list(QuerySpec(page_size=50))
    rows = {x.batch_id: x for x in page.items}
    first, second, balance = rows["7TRANSFER0000001"], rows["7TRANSFER0000002"], rows["BALANCE-CAD"]
    assert (first.gateway, first.state, first.sales_amount, first.sales_count, first.refund_amount, first.fee_amount, first.net_amount) == (
        "PayPal", "Transferred to the bank", Decimal("150.00"), 2, Decimal("15.00"), Decimal("4.60"), Decimal("130.40"))
    assert (first.transfer_amount, first.carried_amount, first.issue) == (Decimal("130.00"), Decimal("-0.40"), "Differs from the transfer")
    assert "hold" in first.notes and "hold released" in first.notes and "deposit from the bank" in first.notes
    assert (first.matched_net, first.difference) == (Decimal("135.00"), Decimal("0.00"))  # Shopify's sales − refunds: all matched
    assert "2 × deposit from the bank (10.00)" in first.notes
    assert (second.net_amount, second.transfer_amount, second.carried_amount) == (Decimal("38.54"), Decimal("38.94"), Decimal("0.40"))
    assert (balance.state, balance.transfer_amount, balance.net_amount, balance.issue) == (
        "In PayPal balance", None, Decimal("11.35"), "Unmatched transactions")  # 4AFTER… is no Shopify payment

    # A second pass moves nothing: the same settlements, the same totals
    r2 = await intake.run_pass(await snap(env, cid))
    async with env.factory(OP) as u:
        again = {x.batch_id: (x.id, x.net_amount, x.transfer_amount) for x in (await SettlementRowRepository(u).list(QuerySpec(page_size=50))).items}
    assert r2.batches == 2 and again == {k: (v.id, v.net_amount, v.transfer_amount) for k, v in rows.items()}

    async with env.factory(OP) as u:
        name, pdf = await SettlementRowService(u).statement_pdf(first.id)
    assert pdf.startswith(b"%PDF") and name == f"settlement-{first.id}.pdf"

    # Found by the gateway, the state or the settlement number; a view per gateway, by month (user 2026-11-19)
    async with env.factory(OP) as u:
        repo = SettlementRowRepository(u)
        assert {x.batch_id for x in (await repo.list(QuerySpec(search="paypal", page_size=50))).items} == set(rows)
        assert [x.batch_id for x in (await repo.list(QuerySpec(search="in paypal balance", page_size=50))).items] == ["BALANCE-CAD"]
        assert [x.id for x in (await repo.list(QuerySpec(search=str(second.id), page_size=50))).items] == [second.id]
        meta = await SettlementRowService(u).meta()
    views = {v["name"]: v["state"] for v in meta["views"]}
    assert {"PayPal — by month", "Authorize.Net — by month"} <= set(views)
    assert views["PayPal — by month"]["filters"] == [{"field": "gateway", "op": "eq", "value": "PayPal"}]
    from app.platform.views import check_state
    for state in views.values():
        assert check_state(state, SettlementRowRepository) == state


async def test_an_other_event_stored_under_the_bare_id_takes_its_suffix(env):  # noqa: F811
    """The first version kept a transfer under PayPal's bare id; a later pass renames it, never doubles it (2026-11-19)."""
    from app.models.gateways import GatewayTransaction
    cid = await paypal_connection(env, datetime(2026, 10, 1, tzinfo=timezone.utc))
    async with env.factory(SYSTEM_ACTOR) as u:
        u.session.add(GatewayTransaction(connection_id=cid, gateway="PAYPAL", external_id="6OLDTRANSFER0001", kind="other",
                                         amount=Decimal("10.00"), currency="CAD", settled=False,
                                         submitted_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                                         raw={"transaction_id": "6OLDTRANSFER0001", "transaction_event_code": "T0400",
                                              "transaction_status": "S"}))
        await u.commit()
    clock = lambda: datetime(2026, 10, 5, tzinfo=timezone.utc)  # noqa: E731
    for _ in range(2):  # twice: the same one row
        await PayPalIntake(uow_factory=env.factory, gateway=FakePayPal(), clock=clock).run_pass(await snap(env, cid))
    ids = [x["external_id"] for x in await env.q("SELECT external_id FROM sales_gateway_transactions WHERE gateway = 'PAYPAL'")]
    assert ids == ["6OLDTRANSFER0001:T0400"]
    batches = [x["external_id"] for x in await env.q("SELECT external_id FROM sales_gateway_settlements WHERE gateway = 'PAYPAL'")]
    assert batches == ["6OLDTRANSFER0001"]  # the settlement keeps the transfer's own id
