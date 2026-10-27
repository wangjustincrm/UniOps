"""Reading the monthly hours figures back out for the rate calculations.

The only interesting behaviour here is what happens when a month is missing.
A rate computed over a period with a gap in it is not a slightly-low rate — it
is a wrong one, and it is wrong in the flattering direction, because the
divisor is too small and the injury rate comes out too high. Or too low, if the
gap is at the end of a bad year. Either way nobody can tell by looking.

So `hours_for` returns the total *and* the months it could not find, and every
caller is expected to say so rather than quietly dividing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hours import HoursWorked
from app.services.day_range import plant_today

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")


@dataclass
class HoursTotal:
    total: Decimal = Decimal(0)
    months_found: int = 0
    months_expected: int = 0
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.months_expected > 0 and not self.missing

    @property
    def usable(self) -> Decimal | None:
        """The divisor, or None when there is nothing to divide by.

        Returned even when months are missing — a partial figure is still the
        best available answer, and the caller reports the gap alongside it
        rather than showing nothing at all.
        """
        return self.total if self.total > 0 else None

    def note(self) -> str | None:
        if self.months_expected == 0:
            return None
        if not self.total:
            return ("Hours worked have not been entered, so the injury rates "
                    "cannot be calculated. HSE enters one figure a month under "
                    "Settings.")
        if self.missing:
            listed = ", ".join(self.missing[:6])
            more = f" and {len(self.missing) - 6} more" if len(self.missing) > 6 else ""
            return (f"Hours are missing for {listed}{more}, so these rates are "
                    "calculated on a short divisor and read higher than they "
                    "should.")
        return None


async def hours_for(
    db: AsyncSession, *, date_from: date, date_to: date
) -> HoursTotal:
    """Total hours over a half-open period, and which months are missing.

    A month counts as inside the period if its first day is. That is the same
    granularity the figures are entered at, and pro-rating a month across a
    period boundary would invent precision the source data does not have.
    """
    months: list[tuple[int, int]] = []
    year, month = date_from.year, date_from.month
    while (year, month) < (date_to.year, date_to.month):
        months.append((year, month))
        month += 1
        if month > 12:
            year, month = year + 1, 1

    if not months:
        return HoursTotal()

    rows = {
        (r.year, r.month): r.hours
        for r in (await db.execute(
            select(HoursWorked).where(
                HoursWorked.year >= months[0][0],
                HoursWorked.year <= months[-1][0],
            ))).scalars()
    }

    result = HoursTotal(months_expected=len(months))
    for year, month in months:
        value = rows.get((year, month))
        if value is None:
            result.missing.append(f"{MONTH_NAMES[month - 1]} {year}")
            continue
        result.total += value
        result.months_found += 1
    return result


async def hours_for_year(db: AsyncSession, year: int) -> HoursTotal:
    """The calendar year to date.

    Months after today are not counted as missing — a year is not incomplete
    because it has not finished yet, and reporting September as absent in
    March would make the note useless.
    """
    today = plant_today()
    end = date(year + 1, 1, 1)
    if year == today.year:
        end = date(today.year, today.month, 1)
        # Include the current month once it has started; the figure for it may
        # legitimately not be in yet, which is what `missing` is for.
        end = date(end.year + (1 if end.month == 12 else 0),
                   1 if end.month == 12 else end.month + 1, 1)
    return await hours_for(db, date_from=date(year, 1, 1), date_to=end)
