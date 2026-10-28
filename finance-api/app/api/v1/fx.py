"""Finance -> Exchange Rates (FX rate module, 2026-10-27).

Read: any finance role. Fetch from Bank of Canada: any finance role (it only
brings in public rates). Manual override / removing an override: finance
managers (`_require_manage`), with a required note.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.coa import _require_manage
from app.core.deps import CurrentUser
from app.core.read_authz import authorize_finance_read
from app.db.base import get_db
from app.models.bank import ExchangeRate, FxFetchRun
from app.services import fx_rates

router = APIRouter(prefix="/fx", tags=["fx-rates"])

# A manual fetch is for a gap or a backfill; a year at a time keeps one click
# from issuing a request the Valet API throttles.
MAX_FETCH_DAYS = 400


def _rate_out(r: ExchangeRate) -> dict:
    return {"id": str(r.id), "from_currency": r.from_currency, "to_currency": r.to_currency,
            "rate": str(r.rate), "effective_date": r.effective_date.isoformat(),
            "source": r.source, "note": r.note,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def _run_out(r: FxFetchRun | None) -> dict | None:
    if r is None:
        return None
    return {"id": str(r.id), "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "trigger": r.trigger, "date_from": r.date_from.isoformat(),
            "date_to": r.date_to.isoformat(), "ok": r.ok,
            "rows_upserted": r.rows_upserted, "message": r.message}


@router.get("/rates")
async def list_rates(user: CurrentUser, db: AsyncSession = Depends(get_db),
                     currency: str | None = Query(default=None),
                     date_from: date | None = Query(default=None),
                     date_to: date | None = Query(default=None),
                     limit: int = Query(default=200, le=2000),
                     offset: int = Query(default=0, ge=0)):
    await authorize_finance_read(db, user)
    base = select(ExchangeRate)
    if currency:
        base = base.where(ExchangeRate.from_currency == currency.upper())
    if date_from:
        base = base.where(ExchangeRate.effective_date >= date_from)
    if date_to:
        base = base.where(ExchangeRate.effective_date <= date_to)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.order_by(ExchangeRate.effective_date.desc(),
                                           ExchangeRate.from_currency)
                             .offset(offset).limit(limit))).scalars().all()
    return {"total": total, "items": [_rate_out(r) for r in rows]}


@router.get("/rate")
async def lookup_rate(user: CurrentUser, currency: str, on: date,
                      db: AsyncSession = Depends(get_db)):
    """The rate a document dated `on` gets — exactly what the AP push uses."""
    await authorize_finance_read(db, user)
    hit = await fx_rates.rate_for(db, currency, on)
    if hit is None:
        return {"currency": currency.upper(), "on": on.isoformat(), "rate": None,
                "effective_date": None, "source": None}
    return {"currency": currency.upper(), "on": on.isoformat(), "rate": str(hit.rate),
            "effective_date": hit.effective_date.isoformat(), "source": hit.source}


@router.get("/status")
async def status(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await authorize_finance_read(db, user)
    last = (await db.execute(select(FxFetchRun).order_by(FxFetchRun.started_at.desc())
                             .limit(1))).scalars().first()
    last_ok = (await db.execute(select(FxFetchRun).where(FxFetchRun.ok.is_(True))
                                .order_by(FxFetchRun.started_at.desc()).limit(1))).scalars().first()
    latest = (await db.execute(
        select(ExchangeRate.from_currency, func.max(ExchangeRate.effective_date), func.count())
        .group_by(ExchangeRate.from_currency).order_by(ExchangeRate.from_currency))).all()
    return {"last_run": _run_out(last), "last_ok_run": _run_out(last_ok),
            "currencies": [{"currency": c, "latest_date": d.isoformat(), "rows": n}
                           for c, d, n in latest],
            "fetch_currencies": await fx_rates.currencies_to_fetch(db)}


class FetchIn(BaseModel):
    date_from: date
    date_to: date | None = None


@router.post("/fetch")
async def fetch(body: FetchIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await authorize_finance_read(db, user)
    date_to = body.date_to or datetime.now(timezone.utc).date()
    if date_to < body.date_from:
        raise HTTPException(status_code=422, detail="date_to is before date_from")
    if (date_to - body.date_from) > timedelta(days=MAX_FETCH_DAYS):
        raise HTTPException(status_code=422,
                            detail=f"Fetch at most {MAX_FETCH_DAYS} days at a time")
    run = await fx_rates.run_fetch(db, date_from=body.date_from, date_to=date_to,
                                   trigger="manual", started_by=uuid.UUID(user["sub"]))
    await db.commit()
    return _run_out(run)


class OverrideIn(BaseModel):
    from_currency: str = Field(min_length=3, max_length=10)
    effective_date: date
    rate: Decimal = Field(gt=0)
    note: str = Field(min_length=1, max_length=255)


@router.put("/rates")
async def override_rate(body: OverrideIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Set a rate by hand (an override of BoC, or a currency BoC does not
    publish). The daily fetch will not overwrite it."""
    await _require_manage(db, user)
    ccy = body.from_currency.upper()
    if ccy == fx_rates.BASE:
        raise HTTPException(status_code=422, detail="CAD is the base currency")
    row = (await db.execute(select(ExchangeRate).where(
        ExchangeRate.from_currency == ccy, ExchangeRate.to_currency == fx_rates.BASE,
        ExchangeRate.effective_date == body.effective_date))).scalar_one_or_none()
    if row is None:
        row = ExchangeRate(from_currency=ccy, to_currency=fx_rates.BASE,
                           effective_date=body.effective_date, rate=body.rate)
        db.add(row)
    row.rate = body.rate
    row.source = fx_rates.SOURCE_MANUAL
    row.note = body.note.strip()
    row.updated_by = uuid.UUID(user["sub"])
    await db.flush()
    await db.commit()
    await db.refresh(row)
    return _rate_out(row)


@router.delete("/rates/{rate_id}")
async def remove_override(rate_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Drop a manual rate. The next fetch brings the BoC rate back for that day."""
    await _require_manage(db, user)
    row = (await db.execute(select(ExchangeRate).where(ExchangeRate.id == rate_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Rate not found")
    if row.source != fx_rates.SOURCE_MANUAL:
        raise HTTPException(status_code=409, detail="Only a manual rate can be removed")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
