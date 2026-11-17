"""Authorize.Net (Finance, user 2026-11-17): the reporting client (reads only, no card data), the
connection's rules, the matching of gateway transactions to Shopify payments, and one pass end to
end — batches, transactions, the cursor, the Payments list's new columns and issues, and the
Settlements list."""
import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import sqlalchemy as sa

from app.domain import gateway_match as gm
from app.domain.connections import check_connection
from app.domain.gateways import kind_of, settled_of
from app.integrations.authorize_net import (
    AuthorizeNetAuthFailed, AuthorizeNetError, AuthorizeNetRefused, AuthorizeNetRequestRefused, FakeAuthorizeNet, HttpAuthorizeNet,
)
from app.models.config import Connection
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import InvalidInput
from app.platform.query import QuerySpec, parse_filter
from app.services.gateway_intake import AuthorizeNetIntake, batch_totals
from app.services.intake import ConnectionSnapshot
from app.services.payment_list import PaymentRowRepository
from app.services.settlement_list import SettlementRowRepository, SettlementRowService
from tests.test_orders import OP, T0, env, shopify_order  # noqa: F401 — env is a fixture

KEY = "s3cretTransactionKey"
CONN = SimpleNamespace(id=1, host="api.authorize.net", username="login123", secret_name="SALES_SECRET_AUTHNET")


def client(handler, secret=None):
    return HttpAuthorizeNet(secret=secret or {"SALES_SECRET_AUTHNET": KEY}.get, transport=httpx.MockTransport(handler),
                            sleep=lambda s: _nothing())


async def _nothing():
    return None


def answer(body: dict, *, bom=True) -> httpx.Response:
    data = json.dumps(body).encode()
    return httpx.Response(200, content=(b"\xef\xbb\xbf" if bom else b"") + data)


OK = {"resultCode": "Ok", "message": [{"code": "I00001", "text": "Successful."}]}


# ── the client ───────────────────────────────────────────────────────────────

async def test_the_client_sends_only_reporting_requests_and_keeps_no_card_data():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((str(req.url), body))
        (name, inner), = body.items()
        assert inner["merchantAuthentication"] == {"name": "login123", "transactionKey": KEY}
        if name == "getSettledBatchListRequest":
            assert (inner["firstSettlementDate"], inner["lastSettlementDate"]) == ("2026-10-01T00:00:00Z", "2026-10-31T00:00:00Z")
            return answer({"batchList": [{"batchId": "900", "settlementTimeUTC": "2026-10-02T06:00:00Z",
                                          "settlementState": "settledSuccessfully", "paymentMethod": "creditCard",
                                          "statistics": [{"accountType": "Visa", "chargeAmount": 150.5, "chargeCount": 2,
                                                          "refundAmount": 20, "refundCount": 1, "declineCount": 1}]}],
                           "messages": OK})
        if name == "getTransactionListRequest":
            assert (inner["batchId"], inner["paging"]) == ("900", {"limit": "1000", "offset": "1"})
            return answer({"transactions": [{"transId": "60001", "submitTimeUTC": "2026-10-01T15:00:00Z",
                                             "transactionStatus": "settledSuccessfully", "invoiceNumber": "9301",
                                             "firstName": "Jane", "lastName": "Doe", "accountType": "Visa",
                                             "accountNumber": "XXXX4242", "settleAmount": 100.5}],
                           "totalNumInResultSet": 1, "messages": OK})
        assert name == "getTransactionDetailsRequest"
        return answer({"transaction": {"transId": "60001", "transactionType": "authCaptureTransaction",
                                       "order": {"invoiceNumber": "9301", "description": "Shopify rzyOxABCDEFG"},
                                       "payment": {"creditCard": {"cardNumber": "XXXX4242", "expirationDate": "XXXX"}},
                                       "billTo": {"firstName": "Jane", "address": "1 rue"}, "customerIP": "1.2.3.4",
                                       "customer": {"email": "jane@example.com"}}, "messages": OK})

    c = client(handler)
    [b] = await c.settled_batches(CONN, datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert (b["batchId"], b["statistics"][0]["chargeAmount"]) == ("900", 150.5)
    page = await c.batch_transactions(CONN, "900", 1)
    assert page.total == 1 and page.transactions == [{
        "transId": "60001", "submitTimeUTC": "2026-10-01T15:00:00Z", "transactionStatus": "settledSuccessfully",
        "invoiceNumber": "9301", "accountType": "Visa", "settleAmount": 100.5}]  # no card number, no names
    d = await c.transaction_details(CONN, "60001")
    assert d == {"transId": "60001", "transactionType": "authCaptureTransaction",
                 "order": {"invoiceNumber": "9301", "description": "Shopify rzyOxABCDEFG"}}
    assert all(u == "https://api.authorize.net/xml/v1/request.api" for u, _ in seen)
    assert [next(iter(b)) for _, b in seen] == ["getSettledBatchListRequest", "getTransactionListRequest",
                                                 "getTransactionDetailsRequest"]
    with pytest.raises(AuthorizeNetRefused):  # never a request that could move money
        await c._call(CONN, "createTransactionRequest", {})


async def test_the_client_refuses_before_sending_and_reads_the_gateway_s_refusals():
    calls = []

    def handler(req):
        calls.append(req)
        code = json.loads(req.content)["authenticateTestRequest"]["merchantAuthentication"]["name"]
        return answer({"messages": {"resultCode": "Error", "message": [{"code": code, "text": "refused"}]}})

    c = client(handler)
    with pytest.raises(AuthorizeNetRefused, match="host"):
        await c.test(SimpleNamespace(**{**vars(CONN), "host": "evil.example.com"}))
    with pytest.raises(AuthorizeNetRefused, match="Transaction Key"):
        await client(handler, secret=lambda n: None).test(CONN)
    assert calls == []  # neither reached the network
    for code, err, text in (("E00007", AuthorizeNetAuthFailed, "refused the login"),
                            ("E00011", AuthorizeNetAuthFailed, "Transaction Details API is not turned on"),
                            ("E00003", AuthorizeNetError, "E00003")):
        with pytest.raises(err, match=text) as e:
            await c.test(SimpleNamespace(**{**vars(CONN), "username": code}))
        assert KEY not in str(e.value)
    with pytest.raises(AuthorizeNetRefused, match="31 days"):
        await c.settled_batches(CONN, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 3, 1, tzinfo=timezone.utc))


def test_the_statuses_say_kind_and_whether_money_moved():
    assert [(kind_of(s), settled_of(s)) for s in ("settledSuccessfully", "refundSettledSuccessfully", "declined", "voided",
                                                  "chargeback", None)] == [
        ("sale", True), ("refund", True), ("sale", False), ("void", False), ("other", False), ("other", False)]
    assert batch_totals({"statistics": [{"chargeAmount": 100.1, "chargeCount": 2, "refundAmount": 5, "refundCount": 1},
                                        {"chargeAmount": "0.9", "chargeCount": 1, "voidCount": 3}]}) == {
        "sales_amount": Decimal("101.00"), "sales_count": 3, "refund_amount": Decimal("5.00"), "refund_count": 1,
        "net_amount": Decimal("96.00"), "declined_count": 0, "voided_count": 3, "chargeback_amount": Decimal("0.00"),
        "chargeback_count": 0, "returned_amount": Decimal("0.00"), "returned_count": 0}


# ── the connection's rules ───────────────────────────────────────────────────

def test_an_authorize_net_connection_reads_only_with_its_login_and_key_name():
    good = {"kind": "AUTHORIZE_NET", "host": "api.authorize.net", "username": "login", "secret_name": "SALES_SECRET_AUTHNET",
            "after_read": "LEAVE", "can_read": True, "can_write": False}
    check_connection(good)
    check_connection(good | {"host": "apitest.authorize.net"})
    for change, field in ((dict(host="api.example.com"), "host"), (dict(username=None), "username"),
                          (dict(secret_name=""), "secret_name"), (dict(can_write=True), "can_write"),
                          (dict(host_key="ssh-ed25519 AAAA"), "host_key"), (dict(inbound_folder="/in"), "inbound_folder"),
                          (dict(partner_code="SHOP"), "partner_code")):
        with pytest.raises(InvalidInput) as e:
            check_connection(good | change)
        assert e.value.details["field"] == field


# ── matching ─────────────────────────────────────────────────────────────────

AT = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)


def P(id, kind="sale", amount="10.00", at=AT, gid=None, pid=None, number=None):
    return gm.ShopifyPayment(id, kind, Decimal(amount), at, gid, pid, number)


def T(key, kind="sale", amount="10.00", at=AT, texts=("", ""), ext=None):
    return gm.GatewayTx(key, kind, Decimal(amount), at, texts, ext or str(key))


def test_matching_takes_the_strongest_rule_first_and_each_side_once():
    payments = [P(1, gid="5001"), P(2, pid="rzyOxQwErTy12345"), P(3, number="9303", amount="30.00"),
                P(4, amount="44.00", at=AT + timedelta(minutes=5)), P(5, kind="refund", gid="5005"),
                P(6, amount="77.00"), P(7, amount="77.00")]
    txs = [T(10, ext="5001"), T(11, texts=("", "Shopify order payment rzyOxQwErTy12345")),
           T(12, amount="30.00", texts=("#9303", "")), T(13, amount="44.00"),
           T(14, kind="sale", ext="5005"),  # Shopify's 5005 is a refund: the kinds differ
           T(15, amount="77.00"), T(16, amount="99.00")]
    got = {m.tx: (m.payment, m.method) for m in gm.match(txs, payments)}
    assert got == {10: (1, "TRANSACTION_ID"), 11: (2, "SHOPIFY_PAYMENT_ID"), 12: (3, "ORDER_NUMBER"), 13: (4, "AMOUNT_TIME")}
    # two Shopify payments of 77.00 at the same time: not one candidate, no match; 99.00 has none
    # a payment id inside another token is not found; order number 930 is not 9303
    later = AT + timedelta(hours=2)
    assert gm.match([T(1, texts=("xrzyOxQwErTy12345", ""))], [P(2, pid="rzyOxQwErTy12345", at=later)]) == []
    # before its details are read, amount and time are not tried
    assert gm.match([gm.GatewayTx(1, "sale", Decimal("10.00"), AT, ("", ""), "1", final=False)], [P(1)]) == []
    assert gm.match([T(1, amount="30.00", texts=("93030", ""))], [P(3, number="9303", amount="30.00", at=AT + timedelta(hours=2))]) == []
    # too far apart in time
    assert gm.match([T(1)], [P(1, at=AT + timedelta(minutes=16))]) == []


# ── one pass, end to end ─────────────────────────────────────────────────────

def batch(bid, at, txs, stats):
    return {"batchId": bid, "settlementTimeUTC": at, "settlementState": "settledSuccessfully", "paymentMethod": "creditCard",
            "statistics": [stats], "transactions": txs}


def gtx(tid, status, amount, at, invoice=None, **more):
    return {"transId": tid, "submitTimeUTC": at, "transactionStatus": status, "invoiceNumber": invoice,
            "accountType": "Visa", "settleAmount": amount, "accountNumber": "XXXX4242", "firstName": "Jane", **more}


async def anet_connection(env, cursor):  # noqa: F811
    return await anet_connection_named(env, "authorize.net", cursor)


async def anet_connection_named(env, name, cursor):  # noqa: F811
    async with env.factory(SYSTEM_ACTOR) as u:
        c = Connection(name=name, kind="AUTHORIZE_NET", host="api.authorize.net", username="login",
                       secret_name="SALES_SECRET_AUTHNET", after_read="LEAVE", can_write=False, cursor_updated_at=cursor,
                       schedule_minutes=60, version=1)
        u.session.add(c)
        await u.session.flush()
        await u.commit()
        return c.id


async def snap(env, cid):  # noqa: F811
    async with env.factory(SYSTEM_ACTOR) as u:
        return ConnectionSnapshot.of(await u.session.get(Connection, cid))


async def test_a_pass_reads_batches_matches_payments_and_feeds_both_lists(env):  # noqa: F811
    tx = lambda id, **kw: {"id": id, "kind": "sale", "status": "success", "currency": "CAD", "test": False,  # noqa: E731
                           "processed_at": "2026-10-01T11:00:00-04:00", **kw}
    env.shop.orders = [shopify_order(1, 9301), shopify_order(2, 9302), shopify_order(3, 9303), shopify_order(4, 9304)]
    env.shop.transactions_by_order |= {
        "1": [tx(101, gateway="authorize_net", amount="100.00", authorization="60001#2281#purchase", payment_id="r1")],
        "2": [tx(201, gateway="authorize.net", amount="100.00", payment_id="rzyOxQwErTy12345")],
        "3": [tx(301, gateway="authorize.net", amount="100.00", payment_id="rAbCdEfGhIj99999"),
              tx(302, gateway="authorize.net", kind="refund", amount="25.00", payment_id="zRefund1234567",
                 processed_at="2026-10-03T10:00:00-04:00")],
        # 4: paid in Shopify, never settled by the gateway (covered by the batches read)
        "4": [tx(401, gateway="authorize.net", amount="100.00", payment_id="rNeverSettled123", processed_at="2026-09-20T10:00:00-04:00")],
    }
    await env.pull()
    anet = FakeAuthorizeNet(page_limit=2)
    anet.batches = [
        batch("900", "2026-09-19T06:00:00Z", [], {"chargeAmount": 0, "chargeCount": 0}),
        batch("901", "2026-10-02T06:00:00Z", [
            gtx("60001", "settledSuccessfully", 100.0, "2026-10-01T15:00:00Z"),
            gtx("60002", "settledSuccessfully", 100.0, "2026-10-01T15:00:30Z"),  # its details name Shopify's payment id
            gtx("60003", "settledSuccessfully", 100.0, "2026-10-01T15:01:00Z", invoice="9303"),
            gtx("60004", "declined", 50.0, "2026-10-01T15:02:00Z"),
            gtx("60005", "settledSuccessfully", 40.0, "2026-10-01T18:00:00Z"),  # typed in the gateway: no Shopify payment
        ], {"chargeAmount": 340.0, "chargeCount": 4, "declineCount": 1}),
        batch("902", "2026-10-04T06:00:00Z", [
            gtx("60010", "refundSettledSuccessfully", 25.0, "2026-10-03T14:00:20Z"),
        ], {"refundAmount": 25.0, "refundCount": 1}),
    ]
    anet.details = {"60002": {"transId": "60002", "order": {"invoiceNumber": None, "description": "Shopify rzyOxQwErTy12345"},
                              "billTo": {"firstName": "Jane"}}}
    cid = await anet_connection(env, datetime(2026, 9, 15, tzinfo=timezone.utc))
    intake = AuthorizeNetIntake(uow_factory=env.factory, gateway=anet, clock=lambda: datetime(2026, 10, 10, tzinfo=timezone.utc))

    r = await intake.run_pass(await snap(env, cid))
    assert (r.status, r.batches, r.batches_read, r.transactions, r.failed) == ("ok", 3, 3, 6, 0)
    assert r.cursor_after == "2026-10-04T06:00:00+00:00"
    assert r.details_read >= 1 and r.matched == 4
    # pages of 2: batch 901's five transactions in three pages
    assert [a for m, a in anet.calls if m == "batch_transactions" and a[0] == "901"] == [("901", 1), ("901", 2), ("901", 3)]

    m = {x["external_id"]: (x["match_method"], x["shopify_id"]) for x in await env.q(
        "SELECT t.external_id, t.match_method, p.shopify_id FROM sales_gateway_transactions t "
        "LEFT JOIN sales_order_payments p ON p.id = t.payment_row_id")}
    assert m == {"60001": ("TRANSACTION_ID", 101), "60002": ("SHOPIFY_PAYMENT_ID", 201), "60003": ("ORDER_NUMBER", 301),
                 "60004": (None, None), "60005": (None, None), "60010": ("AMOUNT_TIME", 302)}

    # no card number and no name anywhere it was stored
    stored = json.dumps([dict(x) for x in await env.q(
        "SELECT encode(content, 'escape') c FROM sales_documents WHERE kind = 'GATEWAY_JSON'")]) + json.dumps(
        [dict(x) for x in await env.q("SELECT raw::text FROM sales_gateway_transactions")])
    assert "4242" not in stored and "Jane" not in stored and "60005" in stored

    # the Payments list
    async with env.factory(OP) as u:
        page = await PaymentRowRepository(u).list(QuerySpec(page_size=200))
    by_ref = {x.gateway_ref: x for x in page.items if x.gateway_ref}
    new = by_ref["60002"]
    assert (new.method, new.shopify_payment_id, new.settlement_ref, new.settled_date.isoformat(), new.match_method,
            new.gateway_status, new.card_type) == ("Authorize.Net", "rzyOxQwErTy12345", "901", "2026-10-02",
                                                   "Shopify payment ID", "settledSuccessfully", "Visa")
    assert (by_ref["60010"].kind, by_ref["60010"].match_method) == ("refund", "Amount and time (probable)")
    only = by_ref["60005"]
    assert (only.order_id, only.issue, only.sales_amount, only.kind) == (None, "In the gateway, not in Shopify", Decimal("40.00"), "sale")
    [never] = [x for x in page.items if x.shopify_payment_id == "rNeverSettled123"]
    assert never.issue == "Not settled in the gateway"
    async with env.factory(OP) as u:
        grouped = await PaymentRowRepository(u).list(QuerySpec(filters=[parse_filter("settlement_ref:eq:901")], page_size=50))
    assert sorted(x.gateway_ref for x in grouped.items) == ["60001", "60002", "60003", "60005"]

    # the Settlements list
    async with env.factory(OP) as u:
        rows = {x.batch_id: x for x in (await SettlementRowRepository(u).list(QuerySpec(page_size=50))).items}
        meta = await SettlementRowService(u).meta()
    b = rows["901"]
    assert (b.sales_amount, b.net_amount, b.matched_net, b.difference, b.unmatched_count, b.declined_count, b.issue) == (
        Decimal("340.00"), Decimal("340.00"), Decimal("300.00"), Decimal("40.00"), 1, 1, "Unmatched transactions")
    assert (rows["902"].net_amount, rows["902"].matched_net, rows["902"].probable_count, rows["902"].issue) == (
        Decimal("-25.00"), Decimal("-25.00"), 1, None)
    assert rows["900"].issue is None and rows["900"].transaction_count == 0
    assert [v["name"] for v in meta["views"]] == ["All", "By month", "Needs a look"]

    # the PDFs: one deposit's statement, and the list as a report
    from tests.test_order_ref import pdf_text
    async with env.factory(OP) as u:
        sname, spdf = await SettlementRowService(u).statement_pdf(b.id)
        rname, rpdf = await SettlementRowService(u).report_pdf(QuerySpec(page_size=50))
    st, rp = pdf_text(spdf).decode("latin-1"), pdf_text(rpdf).decode("latin-1")
    assert sname == f"settlement-{b.id}.pdf" and spdf.startswith(b"%PDF")
    assert "SETTLEMENT" in st and "STATEMENT" in st and f"Settlement {b.id}" in st and "901" in st and "60001" in st and "not in Shopify" in st
    assert "Shopify payment ID" in st and "340.00" in st and "300.00" in st and "40.00" in st
    assert rname == "settlements-2026-09-19-2026-10-04.pdf" and rpdf.startswith(b"%PDF")
    assert "SETTLEMENTS" in rp and "REPORT" in rp and "901" in rp and "902" in rp and "Authorize.Net" in rp and "Unmatched" in rp
    assert "4242" not in st + rp and "Jane" not in st + rp

    # a second pass: nothing new; batches read whole are not read again
    anet.calls.clear()
    r2 = await intake.run_pass(await snap(env, cid))
    assert (r2.status, r2.batches_read, r2.matched) == ("ok", 0, 0)
    assert not [c for c in anet.calls if c[0] == "batch_transactions"]
    assert (await env.q("SELECT count(*) n FROM sales_gateway_transactions"))[0]["n"] == 6


async def test_a_refused_login_latches_and_an_empty_cursor_reads_nothing(env):  # noqa: F811
    anet = FakeAuthorizeNet(fail={"settled_batches": AuthorizeNetAuthFailed("Authorize.Net refused the login (E00007: x)")})
    intake = AuthorizeNetIntake(uow_factory=env.factory, gateway=anet, clock=lambda: T0)
    empty = await anet_connection(env, None)
    assert (await intake.run_pass(await snap(env, empty))).status == "cursor_not_set" and anet.calls == []
    async with env.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(Connection).where(Connection.id == empty).values(cursor_updated_at=T0 - timedelta(days=3)))
        await u.commit()
    r = await intake.run_pass(await snap(env, empty))
    assert r.status == "latched"
    s = await snap(env, empty)
    assert s.auth_failed_at is not None and s.cursor_updated_at == T0 - timedelta(days=3)
    assert (await intake.run_pass(s)).status == "latched" and len(anet.calls) == 1  # skipped until cleared


async def test_a_long_history_is_read_in_31_day_windows_and_the_cursor_follows(env):  # noqa: F811
    anet = FakeAuthorizeNet()
    anet.batches = [batch("1", "2026-01-05T06:00:00Z", [], {}), batch("2", "2026-03-20T06:00:00Z", [], {})]
    cid = await anet_connection(env, datetime(2026, 1, 1, tzinfo=timezone.utc))
    intake = AuthorizeNetIntake(uow_factory=env.factory, gateway=anet, clock=lambda: datetime(2026, 4, 15, tzinfo=timezone.utc))
    r = await intake.run_pass(await snap(env, cid))
    windows = [a for m, a in anet.calls if m == "settled_batches"]
    assert all((b - a).days <= 31 for a, b in windows) and len(windows) == r.windows >= 4
    assert windows[0][0] == datetime(2025, 12, 31, tzinfo=timezone.utc)  # a day of overlap
    assert r.batches == 2 and r.status == "ok"
    # the last window ends now: the cursor stays at the last batch, or the end of a window fully in the past
    assert datetime.fromisoformat(r.cursor_after) >= datetime(2026, 3, 20, 6, tzinfo=timezone.utc)


async def test_voids_chargebacks_and_rows_only_once_their_details_are_read(env):  # noqa: F811
    """Review 2026-11-17: a void Shopify shows as a sale + refund is no money and never "not settled";
    chargebacks are shown on the batch; a gateway-only row waits for its details."""
    tx = lambda id, **kw: {"id": id, "kind": "sale", "status": "success", "currency": "CAD", "test": False,  # noqa: E731
                           "processed_at": "2026-10-01T11:00:00-04:00", **kw}
    env.shop.orders = [shopify_order(5, 9305), shopify_order(6, 9306)]
    env.shop.transactions_by_order |= {
        # the old gateway: the void is a "refund" whose authorization ends in #void, same id as the sale
        "5": [tx(501, gateway="authorize_net", amount="50.00", authorization="70001#1000#purchase", payment_id="r5"),
              tx(502, gateway="authorize_net", kind="refund", amount="50.00", authorization="70001#1000#void", payment_id="z5")],
        # the new app: no id in Shopify; Authorize.Net says voided, its details name the Shopify payment
        "6": [tx(601, gateway="authorize.net", amount="60.00", payment_id="rVoidNewApp12345"),
              tx(602, gateway="authorize.net", kind="refund", amount="60.00", payment_id="zVoidNewApp12345",
                 processed_at="2026-10-01T12:00:00-04:00")],
    }
    await env.pull()
    anet = FakeAuthorizeNet(fail={"transaction_details": AuthorizeNetError("busy")})
    anet.batches = [
        batch("910", "2026-10-02T06:00:00Z", [
            gtx("70001", "voided", 50.0, "2026-10-01T15:00:00Z"),
            gtx("70002", "voided", 60.0, "2026-10-01T15:00:00Z"),
            gtx("70003", "settledSuccessfully", 40.0, "2026-10-01T18:00:00Z"),
            gtx("70004", "chargeback", 15.0, "2026-10-01T19:00:00Z"),
        ], {"chargeAmount": 40.0, "chargeCount": 1, "voidCount": 2, "chargebackAmount": 15.0, "chargebackCount": 1}),
        batch("911", "2026-10-20T06:00:00Z", [], {}),
    ]
    anet.details = {"70002": {"transId": "70002", "order": {"description": "Shopify rVoidNewApp12345"}}}
    cid = await anet_connection(env, datetime(2026, 9, 30, tzinfo=timezone.utc))
    intake = AuthorizeNetIntake(uow_factory=env.factory, gateway=anet, clock=lambda: datetime(2026, 10, 25, tzinfo=timezone.utc))

    async def lists():
        async with env.factory(OP) as u:
            pays = (await PaymentRowRepository(u).list(QuerySpec(page_size=200))).items
            sets = {x.batch_id: x for x in (await SettlementRowRepository(u).list(QuerySpec(page_size=50))).items}
        return pays, sets

    r = await intake.run_pass(await snap(env, cid))
    assert r.details_read == 0  # the details could not be read this pass
    pays, sets = await lists()
    assert not [x for x in pays if x.order_id is None]  # 70003 waits for its details: no gateway-only row yet
    assert (sets["910"].pending_count, sets["910"].unmatched_count) == (1, 0)
    assert (sets["910"].chargeback_amount, sets["910"].other_count, sets["910"].issue) == (Decimal("15.00"), 1, "Chargebacks or returns")

    anet.fail.clear()
    await intake.run_pass(await snap(env, cid))
    pays, sets = await lists()
    by = {x.shopify_payment_id: x for x in pays if x.shopify_payment_id}
    for pid in ("r5", "z5", "rVoidNewApp12345", "zVoidNewApp12345"):
        x = by[pid]
        assert (x.voided, x.sales_amount, x.refund_amount, x.net_amount, x.issue) == (True, 0, 0, 0, None), pid
    assert (by["r5"].match_method, by["rVoidNewApp12345"].match_method) == ("Transaction ID", "Shopify payment ID")
    assert by["r5"].gateway_status == "voided"
    [only] = [x for x in pays if x.order_id is None]
    assert (only.gateway_ref, only.issue) == ("70003", "In the gateway, not in Shopify")
    assert (sets["910"].pending_count, sets["910"].unmatched_count) == (0, 1)


def test_a_void_never_takes_a_sale_by_order_number_or_amount_and_time():
    """Review 2026-11-17: a void then a retry of the same amount 5 minutes later that settled. The
    Shopify sale goes to the settled retry or stays unmatched — never to the void."""
    void = gm.GatewayTx(1, "sale", Decimal("50.00"), AT, ("9301", ""), "1", final=True, void=True)
    retry = gm.GatewayTx(2, "sale", Decimal("50.00"), AT + timedelta(minutes=5), ("9301", ""), "2", final=True)
    sale = P(7, amount="50.00", at=AT + timedelta(minutes=5), number="9301")
    for txs in ([void, retry], [retry, void], [void]):
        assert [m for m in gm.match(txs, [sale]) if m.tx == 1] == []
    assert [(m.tx, m.payment) for m in gm.match([void, retry], [sale])] == [(2, 7)]
    # by an exact id a void still matches: the old gateway's id, or Shopify's payment id in its text
    assert [m.method for m in gm.match([gm.GatewayTx(1, "sale", Decimal("50.00"), AT, ("", ""), "5001", void=True)],
                                       [P(1, amount="50.00", gid="5001")])] == ["TRANSACTION_ID"]


async def test_a_start_older_than_authorize_net_keeps_is_moved_up_to_jan_1_two_years_back(env):  # noqa: F811
    anet = FakeAuthorizeNet()
    cid = await anet_connection(env, datetime(2024, 1, 1, 5, tzinfo=timezone.utc))  # the overlap would ask for 2023
    intake = AuthorizeNetIntake(uow_factory=env.factory, gateway=anet, clock=lambda: datetime(2026, 11, 17, tzinfo=timezone.utc))
    r = await intake.run_pass(await snap(env, cid))
    first = [a for m, a in anet.calls if m == "settled_batches"][0]
    assert first[0] == datetime(2024, 1, 1, tzinfo=timezone.utc) and r.status == "ok"


async def test_a_first_history_read_drains_its_details_in_one_pass_within_the_budget(env, monkeypatch):  # noqa: F811
    import app.services.gateway_intake as gi
    monkeypatch.setattr(gi, "DETAILS_PER_PASS", 2)
    txs = [gtx(str(80000 + i), "settledSuccessfully", 10.0 + i, "2026-10-01T15:00:00Z") for i in range(5)]
    anet = FakeAuthorizeNet()
    anet.batches = [batch("920", "2026-10-02T06:00:00Z", txs, {"chargeAmount": 60.0, "chargeCount": 5})]
    cid = await anet_connection(env, datetime(2026, 9, 30, tzinfo=timezone.utc))
    clock = lambda: datetime(2026, 10, 10, tzinfo=timezone.utc)  # noqa: E731
    r = await AuthorizeNetIntake(uow_factory=env.factory, gateway=anet, clock=clock).run_pass(await snap(env, cid))
    assert r.details_read == 5  # three rounds of 2, 2, 1 in one pass
    # with no budget left after the first round, the rest waits for the next pass
    ticks = iter([0.0, 0.0, 10_000.0])
    anet2 = FakeAuthorizeNet()
    anet2.batches = [batch("921", "2026-10-03T06:00:00Z", [gtx(str(81000 + i), "settledSuccessfully", 5.0, "2026-10-02T15:00:00Z")
                                                             for i in range(5)], {})]
    cid2 = await anet_connection_named(env, "authorize.net 2", datetime(2026, 10, 2, tzinfo=timezone.utc))
    r2 = await AuthorizeNetIntake(uow_factory=env.factory, gateway=anet2, clock=clock, timer=lambda: next(ticks, 10_000.0),
                                  budget_seconds=60).run_pass(await snap(env, cid2))
    assert r2.details_read == 2


async def test_one_transaction_whose_details_are_refused_never_blocks_the_others(env):  # noqa: F811
    """Review 2026-11-17: a refusal about one id is recorded on it and skipped; a transport problem stops the round."""
    t0 = datetime(2026, 10, 10, tzinfo=timezone.utc)
    now = {"t": t0}
    txs = [gtx(str(82000 + i), "settledSuccessfully", 10.0 + i, f"2026-10-01T15:0{i}:00Z") for i in range(4)]
    anet = FakeAuthorizeNet(fail_ids={"82003": AuthorizeNetRequestRefused("Authorize.Net refused getTransactionDetailsRequest (E00099: x)")})
    anet.batches = [batch("930", "2026-10-02T06:00:00Z", txs, {"chargeAmount": 46.0, "chargeCount": 4})]
    cid = await anet_connection(env, datetime(2026, 9, 30, tzinfo=timezone.utc))
    intake = AuthorizeNetIntake(uow_factory=env.factory, gateway=anet, clock=lambda: now["t"])
    r = await intake.run_pass(await snap(env, cid))
    assert r.details_read == 3  # 82003 is the newest, so asked first: the three older ones are still read
    [bad] = await env.q("SELECT raw->'details_failed' f, details_read_at FROM sales_gateway_transactions WHERE external_id = '82003'")
    assert bad["details_read_at"] is None and bad["f"]["attempts"] == 1 and "E00099" in bad["f"]["error"]
    anet.calls.clear()
    await intake.run_pass(await snap(env, cid))  # within a day: not asked again
    assert not [c for c in anet.calls if c[0] == "transaction_details"]
    for day in (2, 4):  # retried once more than a day has passed, up to three attempts in all
        now["t"] = t0 + timedelta(days=day)
        await intake.run_pass(await snap(env, cid))
    [bad] = await env.q("SELECT (raw->'details_failed'->>'attempts')::int n FROM sales_gateway_transactions WHERE external_id = '82003'")
    assert bad["n"] == 3
    now["t"] = t0 + timedelta(days=9)
    anet.calls.clear()
    await intake.run_pass(await snap(env, cid))
    assert not [c for c in anet.calls if c[0] == "transaction_details"]  # given up on
    async with env.factory(OP) as u:
        pays = (await PaymentRowRepository(u).list(QuerySpec(page_size=200))).items
        [b] = [x for x in (await SettlementRowRepository(u).list(QuerySpec(page_size=50))).items if x.batch_id == "930"]
    assert {x.issue for x in pays if x.gateway_ref == "82003"} == {"Details not available from the gateway"}
    assert (b.unmatched_count, b.pending_count) == (4, 0)

    # a transport problem (no Authorize.Net code) stops the round: nothing is marked
    txs2 = [gtx(str(83000 + i), "settledSuccessfully", 5.0, "2026-10-04T15:00:00Z") for i in range(2)]
    anet2 = FakeAuthorizeNet(fail={"transaction_details": AuthorizeNetError("Authorize.Net did not answer in time.")})
    anet2.batches = [batch("931", "2026-10-05T06:00:00Z", txs2, {})]
    cid2 = await anet_connection_named(env, "authorize.net 3", datetime(2026, 10, 4, tzinfo=timezone.utc))
    r2 = await AuthorizeNetIntake(uow_factory=env.factory, gateway=anet2, clock=lambda: t0).run_pass(await snap(env, cid2))
    assert r2.details_read == 0 and len([c for c in anet2.calls if c[0] == "transaction_details"]) == 1
    assert (await env.q("SELECT count(*) n FROM sales_gateway_transactions WHERE external_id LIKE '83%' "
                        "AND raw ? 'details_failed'"))[0]["n"] == 0


async def test_the_statement_names_the_order_of_a_refund_made_in_the_gateway(env):  # noqa: F811
    """A refund done in Authorize.Net itself is no Shopify payment: the statement says so and names
    the order of its original sale (refTransId)."""
    from tests.test_order_ref import pdf_text
    env.shop.orders = [shopify_order(11, 9311)]
    env.shop.transactions_by_order |= {"11": [{
        "id": 1101, "kind": "sale", "status": "success", "currency": "CAD", "test": False, "gateway": "authorize_net",
        "amount": "100.00", "authorization": "61001#1#purchase", "payment_id": "r11", "processed_at": "2026-10-01T11:00:00-04:00"}]}
    await env.pull()
    anet = FakeAuthorizeNet()
    anet.batches = [
        batch("940", "2026-10-02T06:00:00Z", [gtx("61001", "settledSuccessfully", 100.0, "2026-10-01T15:00:00Z")],
              {"chargeAmount": 100.0, "chargeCount": 1}),
        batch("941", "2026-10-06T06:00:00Z", [gtx("61002", "refundSettledSuccessfully", 30.0, "2026-10-05T15:00:00Z")],
              {"refundAmount": 30.0, "refundCount": 1}),
    ]
    anet.details = {"61002": {"transId": "61002", "refTransId": "61001", "order": {}}}
    cid = await anet_connection(env, datetime(2026, 9, 30, tzinfo=timezone.utc))
    await AuthorizeNetIntake(uow_factory=env.factory, gateway=anet,
                             clock=lambda: datetime(2026, 10, 10, tzinfo=timezone.utc)).run_pass(await snap(env, cid))
    async with env.factory(OP) as u:
        [b] = [x for x in (await SettlementRowRepository(u).list(QuerySpec(page_size=50))).items if x.batch_id == "941"]
        _, pdf = await SettlementRowService(u).statement_pdf(b.id)
    st = pdf_text(pdf).decode("latin-1")
    assert (b.difference, b.unmatched_count) == (Decimal("-30.00"), 1)
    assert "61002" in st and "SHOP-9311" in st and "not in Shopify" in st and "-30.00" in st
    assert "are no Shopify payment" in st  # the note above the table


def _row(i, cur, net, issue=None):
    from app.schemas.settlement_list import SettlementRowRead
    from datetime import date
    d = date(2026, 10, 1) + timedelta(days=i)
    n = Decimal(net)
    return SettlementRowRead(id=i, connection_id=1, gateway="Authorize.Net", batch_id=f"B{i}", settled_at=datetime(2026, 10, 1 + i, 6, tzinfo=timezone.utc),
                             settled_date=d, settled_week=d, settled_month=d.replace(day=1), currency=cur, sales_amount=n, sales_count=1,
                             refund_amount=Decimal("0"), refund_count=0, net_amount=n, declined_count=0, voided_count=0,
                             chargeback_amount=Decimal("0"), chargeback_count=0, returned_amount=Decimal("0"), returned_count=0,
                             other_count=0, pending_count=0, transaction_count=1, matched_net=n, difference=Decimal("0"),
                             unmatched_count=0, probable_count=0, issue=issue)


def test_the_report_in_two_currencies_has_a_box_per_currency_and_no_single_total():
    from app.services.pdf_layout import Company
    from app.services.print_docs import settlements_report_pdf_from
    from tests.test_order_ref import pdf_text
    rows = [_row(0, "CAD", "100.00"), _row(1, "USD", "40.00"), _row(2, "CAD", "50.00", issue="Differs from Shopify")]
    t = pdf_text(settlements_report_pdf_from(rows, company=Company("Co", []), period="October 2026", truncated=False)).decode("latin-1")
    assert "NET DEPOSITS \\(CAD\\)" in t and "150.00" in t and "NET DEPOSITS \\(USD\\)" in t and "40.00" in t
    assert "Total \\(" not in t  # no one-currency footer
    assert "Batch B0" in t and "Batch B1" in t and "Differs from" in t
    assert "Only the first rows" not in t
    one = pdf_text(settlements_report_pdf_from(rows[:1], company=Company("Co", []), period="x", truncated=True)).decode("latin-1")
    assert "Total \\(CAD\\)" in one and "Only the first rows are listed" in one


async def test_the_report_stops_at_its_limit_and_says_so(env, monkeypatch):  # noqa: F811
    import app.services.settlement_list as sl
    from tests.test_order_ref import pdf_text
    monkeypatch.setattr(sl, "REPORT_MAX", 2)
    anet = FakeAuthorizeNet()
    anet.batches = [batch(str(950 + i), f"2026-10-0{2 + i}T06:00:00Z", [], {}) for i in range(3)]
    cid = await anet_connection(env, datetime(2026, 9, 30, tzinfo=timezone.utc))
    await AuthorizeNetIntake(uow_factory=env.factory, gateway=anet,
                             clock=lambda: datetime(2026, 10, 10, tzinfo=timezone.utc)).run_pass(await snap(env, cid))
    async with env.factory(OP) as u:
        name, pdf = await SettlementRowService(u).report_pdf(QuerySpec(page_size=50))
    t = pdf_text(pdf).decode("latin-1")
    assert "Only the first rows are listed" in t and "952" in t and "951" in t and "950" not in t  # newest first, 2 of 3
