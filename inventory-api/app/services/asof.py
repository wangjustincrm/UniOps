"""Stock "as of" a date D, for each NC ledger (design §6).

D is a calendar date in Toronto. NC writes business times in its application
server's clock (China, design D14, switchable in Settings), so "everything up
to the end of D in Toronto" becomes a cutoff in NC's wall clock:

    D 23:59:59 America/Toronto  →  (D+1) 11:59:59 / 12:59:59 Asia/Shanghai

★ Except date-only rows. ~12k IC flow rows and ~12k IA rows carry a time of
exactly 00:00:00 — documents MES creates with a date and no time. Converting
"China midnight" would push them to noon of the PREVIOUS Toronto day. A
00:00:00 time is therefore read as a date and compared with D directly.

Per ledger:
  IC  Σ flow with time ≤ cutoff (all of history; flows tie out to on-hand)
  IA  by ACCOUNTING PERIOD (user decision D6): every row of periods before P,
      plus rows of P dated ≤ D. When D is the last day of its month this is
      simply `period ≤ P` — what IA_MONTHNAB reports — regardless of the
      business dates inside P (back-dated entries, user decision D6).
  GL  year(D), periods '00'..P−1 entirely, plus P's vouchers prepared ≤ D.
      On a month end: the whole of P. Unposted included by default (D17).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.services import params

LOCAL = ZoneInfo(params.LOCAL_TIME_ZONE)


@dataclass(frozen=True)
class AsOf:
    d: date
    nc_cutoff: datetime          # naive, NC wall clock
    period: str                  # 'YYYY-MM'
    year: str                    # 'YYYY'
    month: str                   # 'MM'
    month_end: bool
    nc_time_zone: str

    @property
    def sql_params(self) -> dict:
        return {"d": self.d, "cutoff": self.nc_cutoff, "period": self.period,
                "year": self.year, "month": self.month, "month_end": self.month_end}


def make_asof(d: date, nc_time_zone: str) -> AsOf:
    local_end = datetime.combine(d, time(23, 59, 59), tzinfo=LOCAL)
    cutoff = local_end.astimezone(params.zone(nc_time_zone)).replace(tzinfo=None)
    last_day = calendar.monthrange(d.year, d.month)[1]
    return AsOf(d=d, nc_cutoff=cutoff, period=f"{d.year:04d}-{d.month:02d}",
                year=f"{d.year:04d}", month=f"{d.month:02d}", month_end=(d.day == last_day),
                nc_time_zone=nc_time_zone)


def today_local() -> date:
    return datetime.now(LOCAL).date()


def nc_time_le(col: str) -> str:
    """SQL predicate: NC wall-clock column `col` is on or before the as-of
    point. Date-only values (00:00:00) compare as dates; rows with no date at
    all never qualify (they are reported separately as undated)."""
    return (f"(case when {col}::time = time '00:00:00' then {col}::date <= :d "
            f"else {col} <= :cutoff end)")


# Reusable WHERE fragments -----------------------------------------------------

IC_FLOW_ASOF = nc_time_le("f.biz_time")

IA_LEDGER_ASOF = (
    "(l.caccountperiod < :period or (l.caccountperiod = :period and "
    f"(:month_end or {nc_time_le('l.biz_time')})))"
)

GL_LINE_ASOF = (
    "(g.year = :year and (g.period < :month or (g.period = :month and "
    f"(:month_end or {nc_time_le('g.prepared_time')}))))"
)
