"""FX rate module — Bank of Canada daily rates into `exchange_rates`.

User, 2026-10-27: the AP clerks download a rate table and take the rate of the
invoice's issue date. Those are the Bank of Canada daily averages (17 of 27
matched USD bills equal BoC on the invoice date to four decimals), so UniOps
fetches them itself and every consumer reads one history table.

Rules:
* One row per (currency -> CAD, business day). BoC publishes business days
  only; a weekend or holiday takes the previous business day (user, 2026-10-27:
  "the usual practice"), which is what `rate_on` returns.
* A manual row (finance typed it in) is never overwritten by a fetch.
* A lookup further than MAX_LOOKBACK_DAYS before the date is "no rate", so a
  fetch that silently stopped cannot keep stamping a weeks-old rate.
* Never fall back to 1.0 — a caller gets None and decides (the AP push refuses).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ap_invoice import ApInvoice
from app.models.bank import ExchangeRate, FxFetchRun

logger = logging.getLogger(__name__)

BASE = "CAD"
SOURCE_BOC = "boc"
SOURCE_MANUAL = "manual"
# A week covers any run of Canadian statutory holidays plus a weekend.
MAX_LOOKBACK_DAYS = 7
# First date the one-off backfill brings in when the table is empty.
BACKFILL_FROM = date(2025, 1, 1)
# Non-ISO spellings found on UniOps documents -> the ISO code BoC and NC use.
CURRENCY_ALIASES = {"RMB": "CNY"}


def iso_currency(code: str | None) -> str:
    c = (code or "").strip().upper()
    return CURRENCY_ALIASES.get(c, c)


@dataclass(frozen=True)
class FetchedRate:
    currency: str
    effective_date: date
    rate: Decimal


@dataclass(frozen=True)
class RateLookup:
    rate: Decimal
    effective_date: date      # the business day the rate belongs to
    source: str


def series_name(currency: str) -> str:
    return f"FX{currency.upper()}{BASE}"


def parse_valet(payload: dict, currency: str) -> list[FetchedRate]:
    """Valet observations -> rates. Blank / non-numeric values are skipped
    (BoC leaves a value empty on a day it did not publish)."""
    key = series_name(currency)
    out: list[FetchedRate] = []
    for obs in payload.get("observations", []):
        raw = (obs.get(key) or {}).get("v")
        if raw in (None, ""):
            continue
        try:
            rate = Decimal(str(raw))
            d = date.fromisoformat(obs["d"])
        except (InvalidOperation, KeyError, ValueError):
            continue
        if rate > 0:
            out.append(FetchedRate(currency.upper(), d, rate))
    return out


async def fetch_boc(currencies: list[str], date_from: date, date_to: date,
                    client: httpx.AsyncClient | None = None) -> tuple[list[FetchedRate], list[str]]:
    """-> (rates, problems). One request per currency; a currency BoC does not
    publish (404) is a problem, not a failure of the whole fetch."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    rates: list[FetchedRate] = []
    problems: list[str] = []
    try:
        for ccy in currencies:
            url = f"{settings.boc_valet_url}/observations/{series_name(ccy)}/json"
            try:
                r = await client.get(url, params={"start_date": date_from.isoformat(),
                                                  "end_date": date_to.isoformat()})
            except httpx.HTTPError as exc:
                problems.append(f"{ccy}: {exc.__class__.__name__}")
                continue
            if r.status_code == 404:
                problems.append(f"{ccy}: Bank of Canada does not publish this currency")
                continue
            if r.status_code != 200:
                problems.append(f"{ccy}: HTTP {r.status_code}")
                continue
            rates.extend(parse_valet(r.json(), ccy))
    finally:
        if own:
            await client.aclose()
    return rates, problems


async def upsert_boc_rates(db: AsyncSession, rates: list[FetchedRate]) -> int:
    """Insert or refresh BoC rows; a manual row for the same day is left alone."""
    n = 0
    for fr in rates:
        stmt = insert(ExchangeRate).values(
            id=uuid.uuid4(), from_currency=fr.currency, to_currency=BASE,
            rate=fr.rate, effective_date=fr.effective_date, source=SOURCE_BOC,
        ).on_conflict_do_update(
            constraint="uq_exchange_rates_pair_date",
            set_={"rate": fr.rate, "source": SOURCE_BOC, "updated_at": func.now()},
            where=ExchangeRate.source != SOURCE_MANUAL,
        )
        res = await db.execute(stmt)
        n += res.rowcount or 0
    return n


async def currencies_to_fetch(db: AsyncSession) -> list[str]:
    """The configured list plus every non-CAD currency an AP invoice uses."""
    seen = {iso_currency(c) for c in settings.fx_currencies if c and iso_currency(c) != BASE}
    for (c,) in (await db.execute(select(ApInvoice.currency).distinct())).all():
        if c and iso_currency(c) != BASE:
            seen.add(iso_currency(c))
    return sorted(seen)


async def run_fetch(db: AsyncSession, *, date_from: date, date_to: date, trigger: str,
                    started_by: uuid.UUID | None = None, fetcher=None) -> FxFetchRun:
    """Fetch + upsert + record the run. The caller commits."""
    if date_to < date_from:
        raise ValueError("date_to is before date_from")
    run = FxFetchRun(started_at=datetime.now(timezone.utc), trigger=trigger,
                     date_from=date_from, date_to=date_to, started_by=started_by)
    db.add(run)
    await db.flush()
    currencies = await currencies_to_fetch(db)
    try:
        rates, problems = await (fetcher or fetch_boc)(currencies, date_from, date_to)
        run.rows_upserted = await upsert_boc_rates(db, rates)
        # ok = every currency answered. A currency BoC never publishes is
        # reported but still a problem: its rate has to be typed in by hand.
        run.ok = not problems
        run.message = "; ".join(problems) or None
    except Exception as exc:  # noqa: BLE001 — the run row must record any failure
        logger.exception("FX fetch failed")
        run.ok = False
        run.message = f"{exc.__class__.__name__}: {exc}"[:2000]
    run.finished_at = datetime.now(timezone.utc)
    await db.flush()
    return run


async def rate_for(db: AsyncSession, currency: str, as_of: date) -> RateLookup | None:
    """The rate for a document dated `as_of`: that business day's rate, else the
    latest one before it within MAX_LOOKBACK_DAYS. CAD -> 1."""
    ccy = iso_currency(currency)
    if ccy == BASE:
        return RateLookup(Decimal("1"), as_of, SOURCE_BOC)
    row = (await db.execute(
        select(ExchangeRate).where(
            ExchangeRate.from_currency == ccy,
            ExchangeRate.to_currency == BASE,
            ExchangeRate.effective_date <= as_of,
            ExchangeRate.effective_date >= as_of - timedelta(days=MAX_LOOKBACK_DAYS),
        ).order_by(ExchangeRate.effective_date.desc()).limit(1)
    )).scalars().first()
    if row is None:
        return None
    return RateLookup(Decimal(row.rate), row.effective_date, row.source)
