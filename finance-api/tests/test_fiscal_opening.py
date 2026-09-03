"""Opening balances start at the fiscal year's `YYYY-00` voucher, not at time zero.

NC re-states every carried-forward balance as a batch of `YYYY-00` vouchers each
year. Summing every earlier period stacks those restatements on top of each
other — measured on production data 2026-09-03, account 100201 read an opening of
31,769,542.78 against NC's 4,250,430.40. See app/crud/fiscal.py.
"""
import uuid
from datetime import date
from decimal import Decimal

from app.crud import account_balance as ab
from app.crud import fiscal, gl
from app.models.coa import ChartOfAccount
from app.models.journal_voucher import JournalVoucher, JournalVoucherLine

CASH, EQUITY = "1010", "3100"


async def _jv(db, period, *lines, status="posted", cost_center_id=None):
    """One balanced posted voucher in `period`; lines are (account, debit, credit)."""
    jv = JournalVoucher(jv_number=f"JV-{period.replace('-', '')}-{uuid.uuid4().hex[:6]}",
                        voucher_word="JV", voucher_date=date(int(period[:4]), 1, 1),
                        fiscal_period=period, status=status)
    db.add(jv)
    await db.flush()
    for i, (code, dr, cr) in enumerate(lines, start=1):
        db.add(JournalVoucherLine(
            jv_id=jv.id, line_no=i, account_code=code, currency="CAD",
            fx_rate=Decimal("1"), cost_center_id=cost_center_id,
            local_debit=Decimal(dr), local_credit=Decimal(cr)))
    await db.flush()
    return jv


async def _coa(db):
    """1010/3100 come from the COA the migrations seed; create them only if a
    future seed drops them, so account_type-driven reports keep classifying."""
    from sqlalchemy import select
    have = set((await db.execute(select(ChartOfAccount.code).where(
        ChartOfAccount.code.in_([CASH, EQUITY])))).scalars())
    for code, name, typ in ((CASH, "Cash", "asset"), (EQUITY, "Equity", "equity")):
        if code not in have:
            db.add(ChartOfAccount(code=code, name=name, account_type=typ,
                                  is_postable=True))
    await db.flush()


async def _nc_shaped_book(db):
    """Two fiscal years of NC-shaped data: cash opens at 100 in 2025, moves +50,
    and 2026 re-states the resulting 150 as its own `2026-00` opening voucher."""
    await _coa(db)
    await _jv(db, "2025-00", (CASH, "100.00", "0"), (EQUITY, "0", "100.00"))
    await _jv(db, "2025-06", (CASH, "50.00", "0"), (EQUITY, "0", "50.00"))
    await _jv(db, "2026-00", (CASH, "150.00", "0"), (EQUITY, "0", "150.00"))
    await _jv(db, "2026-03", (CASH, "20.00", "0"), (EQUITY, "0", "20.00"))


async def test_opening_period_picks_latest_restatement(db_session):
    await _nc_shaped_book(db_session)
    assert await fiscal.opening_period(db_session, "2026-08") == "2026-00"
    # a period inside the earlier year still starts from that year's own opening
    assert await fiscal.opening_period(db_session, "2025-06") == "2025-00"


async def test_opening_period_is_none_without_restatements(db_session):
    await _coa(db_session)
    await _jv(db_session, "2026-01", (CASH, "10.00", "0"), (EQUITY, "0", "10.00"))
    assert await fiscal.opening_period(db_session, "2026-08") is None


async def test_account_balance_opening_does_not_stack_restatements(db_session):
    await _nc_shaped_book(db_session)
    by = {r["account_code"]: r for r in
          (await ab.account_balance(db_session, "2026-08"))["rows"]}
    # 2026-00 (150) + March (20) — NOT 100 + 50 + 150 + 20 = 320
    assert by[CASH]["opening"] == "170.00"
    assert by[CASH]["closing"] == "170.00"
    # the window cuts on whole periods, and NC's restatement batch is itself a
    # balanced set of vouchers, so the report stays balanced (measured on the
    # prod snapshot: every `YYYY-00` nets to 0.00 across 2020..2026)
    bal = await ab.account_balance(db_session, "2026-08")
    assert bal["balanced"] is True


async def test_prior_year_period_reads_that_years_opening(db_session):
    await _nc_shaped_book(db_session)
    by = {r["account_code"]: r for r in
          (await ab.account_balance(db_session, "2025-06"))["rows"]}
    assert by[CASH]["opening"] == "100.00"      # 2025-00 only
    assert by[CASH]["period_debit"] == "50.00"
    assert by[CASH]["closing"] == "150.00"      # == what 2026-00 re-states


async def test_opening_still_cumulative_without_restatements(db_session):
    """A book posted entirely from UniOps has no `YYYY-00` vouchers and keeps
    the plain cumulative opening — the cut-over `gl_opening` JV lands in a real
    month and must go on being counted."""
    await _coa(db_session)
    await _jv(db_session, "2025-01", (CASH, "100.00", "0"), (EQUITY, "0", "100.00"))
    await _jv(db_session, "2026-02", (CASH, "20.00", "0"), (EQUITY, "0", "20.00"))
    by = {r["account_code"]: r for r in
          (await ab.account_balance(db_session, "2026-08"))["rows"]}
    assert by[CASH]["opening"] == "120.00"


async def test_trial_balance_and_ledger_share_the_window(db_session):
    await _nc_shaped_book(db_session)
    tb = {r["account_code"]: r for r in
          (await gl.trial_balance(db_session, "2026-08"))["rows"]}
    assert tb[CASH]["opening"] == "170.00"
    led = await gl.account_ledger(db_session, CASH, "2026-08")
    assert led["opening"] == "170.00"


async def test_balance_sheet_is_not_inflated_by_restatements(db_session):
    await _nc_shaped_book(db_session)
    bs = await gl.balance_sheet(db_session, "2026-08")
    assets = {a["account_code"]: a["amount"] for a in bs["assets"]}
    assert assets[CASH] == "170.00"


async def test_expand_by_dims_opening_uses_the_window(db_session):
    """② aux expansion carries the same opening, so a dimension row reconciles
    with the account row it sits under."""
    from app.models.mirrors import CostCenter
    cc = CostCenter(id=uuid.uuid4(), code="CC1", name="One")
    db_session.add(cc)
    await db_session.flush()
    await _coa(db_session)
    await _jv(db_session, "2025-00", (CASH, "100.00", "0"), (EQUITY, "0", "100.00"),
              cost_center_id=cc.id)
    await _jv(db_session, "2026-00", (CASH, "100.00", "0"), (EQUITY, "0", "100.00"),
              cost_center_id=cc.id)
    await _jv(db_session, "2026-03", (CASH, "20.00", "0"), (EQUITY, "0", "20.00"),
              cost_center_id=cc.id)
    rows = (await ab.expand_by_dims(db_session, CASH, "2026-03", ["cost_center"]))["rows"]
    assert [r["opening"] for r in rows] == ["100.00"]     # not 200.00
