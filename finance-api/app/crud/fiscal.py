"""Where a GL running balance starts — the fiscal-year opening window.

NC re-states every account's carried-forward balance as a batch of `YYYY-00`
vouchers at the start of each fiscal year (NC's 期初余额). Those vouchers are
*balances*, not movement: summing every period before the reporting one stacks
all of them on top of each other, so a balance that has been carried for N years
is counted N times.

Measured against NC 2026-08 (book 1001A1100000003CGCBX, 2026-09-03): account
100201 read an opening of 31,769,542.78 against NC's 4,250,430.40, and the
27,519,116.38 gap is exactly the 2021-00 … 2026-00 opening vouchers added up.

So an opening balance runs from the LATEST `YYYY-00` at or before the reporting
period. Latest — not the reporting year's own — because a year whose opening
vouchers NC has not written yet then falls back to the prior year's and keeps
accumulating real movement over a longer window: still correct, never zero.

A book with no `YYYY-00` voucher at all (synthetic/test data, a cut-over ledger
posted entirely from UniOps) keeps the plain cumulative behaviour.
"""
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_voucher import POSTED, JournalVoucher


async def opening_period(db: AsyncSession, period: str) -> str | None:
    """The `YYYY-00` period a balance at `period` accumulates from, or None when
    the book has no opening-balance voucher on or before `period`."""
    return (await db.execute(
        select(func.max(JournalVoucher.fiscal_period))
        .where(JournalVoucher.status == POSTED,
               JournalVoucher.fiscal_period.like("%-00"),
               JournalVoucher.fiscal_period <= period))).scalar()


async def opening_window(db: AsyncSession, period: str):
    """Condition over the vouchers making up `period`'s OPENING balance:
    everything from the opening period up to, but excluding, `period` itself."""
    lo = await opening_period(db, period)
    before = JournalVoucher.fiscal_period < period
    return before if lo is None else and_(JournalVoucher.fiscal_period >= lo, before)
