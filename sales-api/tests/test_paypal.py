"""PayPal (Finance, user 2026-11-19): the reporting client — reads only, a token kept until it expires and
never shown, no payer data kept, PayPal's refusals read."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.integrations.paypal import (
    MAX_DAYS, FakePayPal, HttpPayPal, PayPalAuthFailed, PayPalError, PayPalRefused, PayPalRequestRefused, slim_transaction,
)

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
