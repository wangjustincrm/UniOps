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

This module also owns the other half of "which vouchers does a GL read count" —
the posted/unposted basis — because the two travel together: a report folding in
unposted vouchers must fold them into its opening as well, or its opening and
its movement are measured on different books.
"""
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_voucher import POSTED, JournalVoucher


# NC's 科目余额表 carries a 包含未记账凭证 toggle. NC's ledger counts a voucher
# only once NC tallies it (记账); the report can optionally fold in the ones that
# have merely been entered. nc_sync keeps that fact in `status` — a mirrored
# voucher posts when NC tallies it and stays draft until then — so "include
# unposted" is every status a voucher holds before it is cancelled. UniOps' own
# not-yet-posted JVs ride along under the same reading: entered, not in the
# ledger. `reversed` is cancelled and never counts either way.
#
# An NC voucher counts as unposted only while NC still holds it as a normal
# voucher. The mirror also keeps drafts NC has discarded, temp-saved, flagged in
# error, or deleted since import (nc_voucher_state) — none of them will ever be
# tallied, and the Budget pages' "include unposted" box would otherwise count
# them as spend. UniOps' own JVs carry no NC state (NULL) and are unaffected.
UNPOSTED = ("draft", "reviewed")
LIVE_NC_STATE = "normal"


def status_filter(include_unposted: bool = False):
    """Which vouchers a GL read counts."""
    if not include_unposted:
        return JournalVoucher.status == POSTED
    return or_(JournalVoucher.status == POSTED,
               and_(JournalVoucher.status.in_(UNPOSTED),
                    or_(JournalVoucher.nc_voucher_state.is_(None),
                        JournalVoucher.nc_voucher_state == LIVE_NC_STATE)))


def status_sql(alias: str = "v", include_unposted: bool = False) -> str:
    """status_filter() as SQL text over `alias`.journal_vouchers, for the reports
    written as raw SQL (budget_rollup). Constant-only — nothing user-supplied is
    interpolated. tests/test_untallied_basis.py holds the two to the same rows."""
    posted = f"{alias}.status = '{POSTED}'"
    if not include_unposted:
        return posted
    unposted = ", ".join(f"'{s}'" for s in UNPOSTED)
    return (f"({posted} or ({alias}.status in ({unposted}) and "
            f"coalesce({alias}.nc_voucher_state, '{LIVE_NC_STATE}') = '{LIVE_NC_STATE}'))")


async def opening_period(db: AsyncSession, period: str,
                         include_unposted: bool = False) -> str | None:
    """The `YYYY-00` period a balance at `period` accumulates from, or None when
    the book has no opening-balance voucher on or before `period`."""
    return (await db.execute(
        select(func.max(JournalVoucher.fiscal_period))
        .where(status_filter(include_unposted),
               JournalVoucher.fiscal_period.like("%-00"),
               JournalVoucher.fiscal_period <= period))).scalar()


async def opening_window(db: AsyncSession, period: str,
                         include_unposted: bool = False):
    """Condition over the vouchers making up `period`'s OPENING balance:
    everything from the opening period up to, but excluding, `period` itself."""
    lo = await opening_period(db, period, include_unposted)
    before = JournalVoucher.fiscal_period < period
    return before if lo is None else and_(JournalVoucher.fiscal_period >= lo, before)
