"""Payment, derived (user 2026-11-23): a Shopify order is Settled (auto) when every captured payment is a gateway
transaction (as Payments matched it) in a closed settlement; its date and reference are that settlement's. The manual
mark always wins, and undoing it falls back to this. One rule: the SQL (lists, read) and the Python agree on the same
fixtures."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.gateways import PAYPAL_IN_BALANCE
from app.domain.order_payment import NOT_NEEDED, NOT_SETTLED, SETTLED, WAITING, payment_status
from app.models.gateways import GatewaySettlement, GatewayTransaction
from app.models.payments import OrderPayment
from app.platform.actors import SYSTEM_ACTOR
from tests.test_order_payment import ACT, FINANCE, SETTLE_INPUT, _exec, _orders, _status, api, one  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture

T = datetime(2026, 11, 10, 20, tzinfo=timezone.utc)
CLOSED = "settledSuccessfully"
_n = iter(range(1, 10_000))


async def _pay(api, order_id: int, *, gateway="paypal", amount="100.00", tx=None, kind="sale", status="success"):  # noqa: F811
    """A Shopify payment and, with `tx` = (settlement state | None), its matched gateway transaction (state None:
    matched, in no settlement)."""
    n = next(_n)
    async with api.env.factory(SYSTEM_ACTOR) as u:
        s = u.session
        p = OrderPayment(order_id=order_id, shopify_id=900_000 + n, kind=kind, gateway=gateway, status=status,
                         amount=Decimal(amount), currency="CAD", processed_at=T, raw={})
        s.add(p)
        await s.flush()
        if tx is not None:
            gw = "PAYPAL" if gateway == "paypal" else "AUTHORIZE_NET"
            sid = None
            if tx != "none":
                st = GatewaySettlement(connection_id=api.env.conn_id, gateway=gw, external_id=f"S{n}", currency="CAD",
                                       settled_at=T + timedelta(days=n % 5), state=tx, raw={})
                s.add(st)
                await s.flush()
                sid = st.id
            s.add(GatewayTransaction(connection_id=api.env.conn_id, gateway=gw, external_id=f"T{n}", settlement_id=sid,
                                     kind="sale", status="ok", settled=tx not in ("none",), amount=Decimal(amount),
                                     currency="CAD", submitted_at=T, payment_row_id=p.id, raw={}))
        await u.commit()


# Each case: its payments, then what the order's Payment is (the user's cases, 2026-11-23)
CASES = {
    "9701": ([dict(tx=CLOSED, gateway="authorize.net")], SETTLED),                         # fully in a settlement
    "9702": ([dict(tx=PAYPAL_IN_BALANCE)], WAITING),                                        # in the PayPal balance
    "9703": ([dict(tx="none")], WAITING),                                                   # matched, not settled yet
    "9704": ([dict(tx="Transferred to the bank"), dict(tx=PAYPAL_IN_BALANCE)], NOT_SETTLED),  # partly settled
    "9705": ([dict(gateway="AlphaPay")], NOT_SETTLED),                                      # not via our gateways
    "9706": ([dict()], NOT_SETTLED),                                                        # not matched yet
    "9708": ([dict(tx=CLOSED, gateway="authorize_net")], SETTLED),                          # the store's old name of it
    "9707": ([dict(tx=CLOSED, status="failure")], NOT_SETTLED),                             # nothing captured
}


def _python(pays, *, historical=False, net_zero=False):
    ok = [p for p in pays if p.get("status", "success") == "success"]
    ours = [p for p in ok if p.get("gateway", "paypal") in ("paypal", "authorize.net", "authorize_net")]
    matched = [p for p in ours if "tx" in p]
    closed = [p for p in matched if p["tx"] not in ("none", PAYPAL_IN_BALANCE)]
    return payment_status(draft=False, cancelled=False, settled=False, invoice_sent=False, invoice_date=None, terms=None,
                          today=date(2026, 11, 23), historical=historical, shopify=True, net_zero=net_zero,
                          captured=len(ok), other=len(ok) - len(ours), matched=len(matched), closed=len(closed))


async def test_the_sql_and_the_python_rule_agree_on_every_case(api):  # noqa: F811
    o = await _orders(api, *CASES)
    for n, (pays, want) in CASES.items():
        for p in pays:
            await _pay(api, o[n]["id"], **p)
        got = (await _status(api, o[n]["id"]))["payment_status"]
        assert (got, _python(pays)) == (want, want), n
    r = (await api.get("/api/v1/orders?filter=payment_status:eq:Settled", headers=FINANCE)).json()
    assert sorted(x["source_order_number"] for x in r["items"]) == ["9701", "9708"]  # the list filters on the same rule
    auto = await _status(api, o["9701"]["id"])
    assert auto["settled_on"] is None and auto["auto_settlement_label"].startswith("Authorize.Net settlement S")
    assert auto["auto_settled_on"] and auto["auto_settlement_id"]


async def test_a_historical_order_shows_what_its_gateway_proves_and_is_never_chased(api):  # noqa: F811
    """User 2026-11-24: Settled (auto), with its settlement, for the old orders too; what is not proved is Not needed."""
    cases = {"9741": ([dict(tx=CLOSED, gateway="authorize_net")], SETTLED), "9742": ([dict(tx=PAYPAL_IN_BALANCE)], WAITING),
             "9743": ([dict(gateway="AlphaPay")], NOT_NEEDED), "9744": ([dict()], NOT_NEEDED), "9745": ([], NOT_NEEDED),
             "9746": ([dict(tx=CLOSED, gateway="authorize.net"), dict(gateway="AlphaPay")], NOT_NEEDED)}
    o = await _orders(api, *cases)
    for n, (pays, want) in cases.items():
        for p in pays:
            await _pay(api, o[n]["id"], **p)
        await _exec(api, f"UPDATE sales_orders SET historical = true WHERE id = {o[n]['id']}")
        got = await _status(api, o[n]["id"])
        assert (got["payment_status"], _python(pays, historical=True)) == (want, want), n
        assert (got["auto_settlement_label"] is not None) == (want == SETTLED), n
    q = "filter=payment_status:in:Overdue|Due|Not settled"
    assert (await api.get(f"/api/v1/orders?{q}", headers=FINANCE)).json()["total"] == 0  # none of them is To settle


async def test_nothing_left_to_receive_needs_no_payment(api):  # noqa: F811
    o = await _orders(api, 9711, 9712)
    for n in ("9711", "9712"):
        await _pay(api, o[n]["id"], tx=PAYPAL_IN_BALANCE)
    await _exec(api, f"UPDATE sales_orders SET net_amount = 0, total_refunded = total WHERE id = {o['9711']['id']}")
    await _exec(api, f"UPDATE sales_orders SET net_amount = 0, total_refunded = total WHERE id = {o['9712']['id']}")
    pays = [dict(tx=PAYPAL_IN_BALANCE)]
    assert [(await _status(api, o[n]["id"]))["payment_status"] for n in ("9711", "9712")] == [NOT_NEEDED, NOT_NEEDED]
    assert _python(pays, net_zero=True) == NOT_NEEDED


async def test_the_manual_mark_wins_and_undo_falls_back_to_the_gateway(api):  # noqa: F811
    o = await _orders(api, 9721, 9722)
    a, b = o["9721"], o["9722"]
    await _pay(api, a["id"], tx=CLOSED, gateway="authorize.net")
    auto = await _status(api, a["id"])
    # an order the gateway settled: Mark settled is skipped with the settlement's name; Undo has nothing to undo
    r = (await api.post(ACT + "settle", json={"ids": {str(a["id"]): auto["version"]}, "input": SETTLE_INPUT}, headers=FINANCE)).json()
    assert r["results"][0]["status"] == "skipped" and r["results"][0]["reason"] == f"already settled by {auto['auto_settlement_label']}"
    r = (await api.post(ACT + "unsettle", json={"ids": {str(a["id"]): auto["version"]}, "input": {"reason": "test it"}},
                        headers=FINANCE)).json()
    assert r["results"][0]["status"] == "skipped" and "nothing to undo" in r["results"][0]["reason"]
    # marked by hand first: the mark wins; its undo falls back to what the gateway says
    assert (await one(api, "settle", b, input=SETTLE_INPUT))["status"] == "done"
    await _pay(api, b["id"], tx=CLOSED, gateway="authorize.net")
    s = await _status(api, b["id"])
    assert (s["payment_status"], s["settled_reference"], s["auto_settlement_id"]) == (SETTLED, "REM 77", None)
    assert (await one(api, "unsettle", s, input={"reason": "wrong remittance"}))["status"] == "done"
    s = await _status(api, b["id"])
    assert (s["payment_status"], s["settled_on"]) == (SETTLED, None) and s["auto_settlement_label"].startswith("Authorize.Net")


@pytest.mark.parametrize("state", [PAYPAL_IN_BALANCE, "none"])
async def test_waiting_for_the_gateway_is_not_in_to_settle(api, state):  # noqa: F811
    o = (await _orders(api, 9731))["9731"]
    await _pay(api, o["id"], tx=state)
    q = "filter=payment_status:in:Overdue|Due|Not settled"
    assert (await api.get(f"/api/v1/orders?{q}", headers=FINANCE)).json()["total"] == 0  # To settle's filter
