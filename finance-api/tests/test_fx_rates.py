"""FX rate module — Bank of Canada fetch, lookup rules, override API, schedule."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings as app_settings
from app.db.base import get_db
from app.main import app
from app.models.bank import ExchangeRate, FxFetchRun
from app.services import fx_rates
from app.services.fx_rates import FetchedRate
from app.tasks.fx_rate_scheduler import is_due


def _h(role="finance_manager"):
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     app_settings.jwt_secret_key, algorithm=app_settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session
    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def test_parse_valet_skips_blank_days():
    payload = {"observations": [
        {"d": "2026-10-09", "FXUSDCAD": {"v": "1.4271"}},
        {"d": "2026-10-12", "FXUSDCAD": {"v": ""}},          # holiday, published blank
        {"d": "2026-10-13", "FXUSDCAD": {"v": "1.4222"}},
    ]}
    got = fx_rates.parse_valet(payload, "usd")
    assert [(r.currency, r.effective_date.isoformat(), str(r.rate)) for r in got] == [
        ("USD", "2026-10-09", "1.4271"), ("USD", "2026-10-13", "1.4222")]


def test_rmb_is_cny():
    assert fx_rates.iso_currency("rmb") == "CNY"
    assert fx_rates.series_name("CNY") == "FXCNYCAD"


async def _fake_fetcher(rates, problems=()):
    async def f(currencies, d0, d1):
        return list(rates), list(problems)
    return f


async def test_run_fetch_upserts_and_never_overwrites_manual(db_session):
    db_session.add(ExchangeRate(from_currency="USD", to_currency="CAD", rate=Decimal("1.5"),
                                effective_date=date(2026, 10, 13), source="manual", note="bank deal"))
    await db_session.flush()
    fetch = await _fake_fetcher([
        FetchedRate("USD", date(2026, 10, 9), Decimal("1.4271")),
        FetchedRate("USD", date(2026, 10, 13), Decimal("1.4222")),
    ])
    run = await fx_rates.run_fetch(db_session, date_from=date(2026, 10, 1),
                                   date_to=date(2026, 10, 14), trigger="manual", fetcher=fetch)
    assert run.ok is True and run.finished_at is not None
    rows = {r.effective_date: r for r in (await db_session.execute(select(ExchangeRate))).scalars()}
    assert rows[date(2026, 10, 9)].rate == Decimal("1.4271") and rows[date(2026, 10, 9)].source == "boc"
    # the manual row survives the fetch untouched
    assert rows[date(2026, 10, 13)].rate == Decimal("1.5") and rows[date(2026, 10, 13)].source == "manual"

    # a second fetch refreshes a BoC row in place (no duplicate)
    fetch2 = await _fake_fetcher([FetchedRate("USD", date(2026, 10, 9), Decimal("1.4300"))])
    await fx_rates.run_fetch(db_session, date_from=date(2026, 10, 9), date_to=date(2026, 10, 9),
                             trigger="manual", fetcher=fetch2)
    db_session.expire_all()          # the upsert is a core statement; drop cached rows
    oct9 = (await db_session.execute(select(ExchangeRate).where(
        ExchangeRate.effective_date == date(2026, 10, 9)))).scalars().all()
    assert len(oct9) == 1 and oct9[0].rate == Decimal("1.4300")


async def test_run_fetch_records_problems(db_session):
    fetch = await _fake_fetcher([], ["XYZ: Bank of Canada does not publish this currency"])
    run = await fx_rates.run_fetch(db_session, date_from=date(2026, 10, 1),
                                   date_to=date(2026, 10, 2), trigger="schedule", fetcher=fetch)
    assert run.ok is False and "XYZ" in run.message


async def test_rate_for_takes_previous_business_day_within_a_week(db_session):
    db_session.add_all([
        ExchangeRate(from_currency="USD", to_currency="CAD", rate=Decimal("1.4271"),
                     effective_date=date(2026, 10, 9), source="boc"),      # Friday
        ExchangeRate(from_currency="CNY", to_currency="CAD", rate=Decimal("0.1950"),
                     effective_date=date(2026, 10, 9), source="boc"),
    ])
    await db_session.flush()
    # Sunday 11th + Thanksgiving Monday 12th -> Friday's rate (the usual practice)
    for d in (date(2026, 10, 9), date(2026, 10, 11), date(2026, 10, 12)):
        hit = await fx_rates.rate_for(db_session, "USD", d)
        assert hit.rate == Decimal("1.4271") and hit.effective_date == date(2026, 10, 9)
    # more than a week stale = no rate, never a silent old one
    assert await fx_rates.rate_for(db_session, "USD", date(2026, 10, 17)) is None
    # nothing before the first rate
    assert await fx_rates.rate_for(db_session, "USD", date(2026, 10, 8)) is None
    assert (await fx_rates.rate_for(db_session, "CAD", date(2026, 1, 1))).rate == 1
    assert (await fx_rates.rate_for(db_session, "RMB", date(2026, 10, 10))).rate == Decimal("0.1950")


def test_schedule_due_once_per_toronto_day_after_publish():
    # 2026-10-27 21:30 UTC = 17:30 Toronto (EDT)
    after = datetime(2026, 10, 27, 21, 30, tzinfo=timezone.utc)
    before = datetime(2026, 10, 27, 20, 30, tzinfo=timezone.utc)   # 16:30 Toronto
    assert is_due(now=before, last_ok_started_at=None) is False
    assert is_due(now=after, last_ok_started_at=None) is True
    # done today after publish -> not again
    assert is_due(now=after + timedelta(hours=2), last_ok_started_at=after) is False
    # done yesterday -> due today
    assert is_due(now=after, last_ok_started_at=after - timedelta(days=1)) is True
    # a run this morning (before BoC published) does not count for today
    assert is_due(now=after, last_ok_started_at=before - timedelta(hours=8)) is True


async def test_override_needs_manager_and_a_note(client, db_session):
    body = {"from_currency": "usd", "effective_date": "2026-10-13", "rate": "1.43", "note": "per bank"}
    r = await client.put("/finance/v1/fx/rates", json=body, headers=_h("ap_clerk"))
    assert r.status_code == 403
    r = await client.put("/finance/v1/fx/rates", json={**body, "note": ""}, headers=_h())
    assert r.status_code == 422
    r = await client.put("/finance/v1/fx/rates", json=body, headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "manual" and r.json()["from_currency"] == "USD"

    r = await client.get("/finance/v1/fx/rate", params={"currency": "USD", "on": "2026-10-14"},
                         headers=_h("ap_clerk"))
    assert r.status_code == 200 and r.json()["rate"].startswith("1.43")
    assert r.json()["effective_date"] == "2026-10-13"

    rid = (await client.get("/finance/v1/fx/rates", headers=_h("ap_clerk"))).json()["items"][0]["id"]
    assert (await client.delete(f"/finance/v1/fx/rates/{rid}", headers=_h("ap_clerk"))).status_code == 403
    assert (await client.delete(f"/finance/v1/fx/rates/{rid}", headers=_h())).status_code == 200


async def test_fx_read_needs_a_finance_role(client):
    assert (await client.get("/finance/v1/fx/rates", headers=_h("requester"))).status_code == 403
    assert (await client.get("/finance/v1/fx/status", headers=_h("ap_clerk"))).status_code == 200


async def test_status_lists_currencies_incl_ap_currencies(client, db_session):
    from app.models.ap_invoice import ApInvoice
    db_session.add(ApInvoice(ap_invoice_number="AP-FX-1", source="epms", source_invoice_id=uuid.uuid4(),
                             currency="RMB", invoice_date=date(2026, 10, 1), status="posted"))
    await db_session.flush()
    st = (await client.get("/finance/v1/fx/status", headers=_h())).json()
    assert "CNY" in st["fetch_currencies"] and "USD" in st["fetch_currencies"]
    assert "RMB" not in st["fetch_currencies"] and "CAD" not in st["fetch_currencies"]


async def test_fetch_window_is_capped(client):
    r = await client.post("/finance/v1/fx/fetch", json={"date_from": "2024-01-01", "date_to": "2026-10-27"},
                          headers=_h())
    assert r.status_code == 422


async def test_fetch_endpoint_records_a_run(client, db_session, monkeypatch):
    async def fake(currencies, d0, d1):
        return [FetchedRate("USD", d0, Decimal("1.40"))], []
    monkeypatch.setattr(fx_rates, "fetch_boc", fake)
    r = await client.post("/finance/v1/fx/fetch", json={"date_from": "2026-10-01", "date_to": "2026-10-02"},
                          headers=_h("ap_clerk"))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["trigger"] == "manual"
    runs = (await db_session.execute(select(FxFetchRun))).scalars().all()
    assert len(runs) == 1 and runs[0].rows_upserted == 1
