"""General Ledger — the closed loop over the posting spine (Phase f).

Every sub-ledger (AP accrual, payments, AR revenue, receipts, expenses) already
emits balanced double-entry posting lines with account_code + fiscal_period, so
the GL is a read/aggregation layer plus two write flows:

  * opening balances — post a migrated (NC65) trial balance as one balanced
    `opening` journal at cut-over
  * year-end close   — sweep revenue/expense balances into Retained Earnings
    (`closing` journal), resetting P&L for the new year

Reporting basis (Plan 5): the GL reads POSTED journal_vouchers only —
business-event JVs are born draft and enter the GL when finance reviews and
posts them; opening/close/NC-import vouchers post immediately (system-
authoritative). posting_events remain as the business spine / Document Chain.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.fiscal import opening_period, opening_window
from app.models.coa import ChartOfAccount
from app.models.journal_voucher import POSTED, JournalVoucher, JournalVoucherLine
from app.models.posting import PostingEvent, PostingLine
from app.services.posting import emit_event

_ZERO = Decimal("0")
_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # stable namespace for GL doc ids
RETAINED_EARNINGS = "3100"
CURRENT_YEAR_EARNINGS = "3200"


def _s(v: Decimal) -> str:
    return str(Decimal(v).quantize(Decimal("0.01")))


async def _coa_map(db: AsyncSession) -> dict[str, ChartOfAccount]:
    rows = (await db.execute(select(ChartOfAccount))).scalars().all()
    return {a.code: a for a in rows}


def _year_start(period: str) -> str:
    return f"{period[:4]}-01"


# ── trial balance ─────────────────────────────────────────────────────────────────

async def trial_balance(db: AsyncSession, period: str) -> dict:
    """Opening (cumulative before `period`) + period movement + closing, per
    account. Closing total debit must equal credit (balanced by construction)."""
    coa = await _coa_map(db)

    async def sums(where):
        q = (select(JournalVoucherLine.account_code,
                    func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                    func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
             .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
             .where(JournalVoucher.status == POSTED).where(where)
             .group_by(JournalVoucherLine.account_code))
        return {code: (Decimal(d), Decimal(c)) for code, d, c in (await db.execute(q)).all()}

    opening = await sums(await opening_window(db, period))
    movement = await sums(JournalVoucher.fiscal_period == period)

    codes = sorted(set(opening) | set(movement), key=lambda c: (c is None, c or ""))
    rows = []
    tot_open = tot_dr = tot_cr = tot_close = _ZERO
    for code in codes:
        od, oc = opening.get(code, (_ZERO, _ZERO))
        md, mc = movement.get(code, (_ZERO, _ZERO))
        open_bal = od - oc
        close_bal = open_bal + md - mc
        acct = coa.get(code)
        rows.append({
            "account_code": code or "(unmapped)",
            "account_name": acct.name if acct else "(unmapped)",
            "account_type": acct.account_type if acct else None,
            "opening": _s(open_bal),
            "period_debit": _s(md), "period_credit": _s(mc),
            "closing": _s(close_bal),
        })
        tot_open += open_bal; tot_dr += md; tot_cr += mc; tot_close += close_bal
    return {
        "period": period,
        "rows": rows,
        "totals": {"opening": _s(tot_open), "period_debit": _s(tot_dr),
                   "period_credit": _s(tot_cr), "closing": _s(tot_close)},
        "balanced": tot_dr == tot_cr and tot_close == _ZERO,
    }


# ── account ledger (GL detail) ──────────────────────────────────────────────────

async def account_ledger(db: AsyncSession, code: str, period: str) -> dict:
    coa = await _coa_map(db)
    acct = coa.get(code)

    since_opening = await opening_window(db, period)
    opening_row = (await db.execute(
        select(func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
               func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
        .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
        .where(JournalVoucherLine.account_code == code,
               JournalVoucher.status == POSTED, since_opening)
    )).one()
    running = Decimal(opening_row[0]) - Decimal(opening_row[1])
    opening = running

    lines = (await db.execute(
        select(JournalVoucherLine, JournalVoucher)
        .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
        .where(JournalVoucherLine.account_code == code,
               JournalVoucher.status == POSTED,
               JournalVoucher.fiscal_period == period)
        .order_by(JournalVoucher.voucher_date, JournalVoucher.jv_number,
                  JournalVoucherLine.line_no)
    )).all()

    entries = []
    for ln, jv in lines:
        running += ln.local_debit - ln.local_credit
        entries.append({
            "date": jv.voucher_date.isoformat(),
            "source": f"{jv.source_doc_type}:{jv.source_doc_number}"
                      if jv.source_doc_type else jv.jv_number,
            "partner_name": ln.partner_name, "memo": ln.summary,
            "debit": _s(ln.local_debit), "credit": _s(ln.local_credit),
            "balance": _s(running),
        })
    return {
        "account_code": code,
        "account_name": acct.name if acct else "(unmapped)",
        "period": period, "opening": _s(opening), "closing": _s(running),
        "entries": entries,
    }


# ── journal (posted JV voucher list) ────────────────────────────────────────────

async def journal(db: AsyncSession, period: str, limit: int = 200) -> list[dict]:
    """Posted journal vouchers as journal entries (was posting_events pre-Plan-5)."""
    jvs = (await db.execute(
        select(JournalVoucher)
        .where(JournalVoucher.status == POSTED, JournalVoucher.fiscal_period == period)
        .order_by(JournalVoucher.voucher_date.desc(), JournalVoucher.jv_number.desc())
        .limit(limit)
    )).scalars().all()
    if not jvs:
        return []
    ids = [j.id for j in jvs]
    lines = (await db.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id.in_(ids))
        .order_by(JournalVoucherLine.line_no)
    )).scalars().all()
    by_jv: dict[uuid.UUID, list] = {}
    for ln in lines:
        by_jv.setdefault(ln.jv_id, []).append(ln)
    out = []
    for jv in jvs:
        jlines = by_jv.get(jv.id, [])
        out.append({
            "jv_id": str(jv.id), "jv_number": jv.jv_number,
            "date": jv.voucher_date.isoformat(),
            "source": f"{jv.source_doc_type}:{jv.source_doc_number}"
                      if jv.source_doc_type else jv.jv_number,
            "summary": jv.summary,
            "lines": [{"account_code": l.account_code, "partner_name": l.partner_name,
                       "summary": l.summary, "debit": _s(l.local_debit),
                       "credit": _s(l.local_credit), "currency": l.currency}
                      for l in jlines],
        })
    return out


# ── financial statements ────────────────────────────────────────────────────────

async def _balances_through(db: AsyncSession, period_lo: str | None, period_hi: str,
                            exclude_closing: bool = False):
    """{code: signed local balance (debit-positive)} over POSTED JVs in [lo, hi].
    exclude_closing drops year-end close vouchers (source_doc_type='gl_close',
    NULL-safe) so close_year measures operational P&L idempotently."""
    conds = [JournalVoucher.status == POSTED, JournalVoucher.fiscal_period <= period_hi]
    if period_lo is not None:
        conds.append(JournalVoucher.fiscal_period >= period_lo)
    if exclude_closing:
        conds.append(or_(JournalVoucher.source_doc_type.is_(None),
                         JournalVoucher.source_doc_type != "gl_close"))
    q = (select(JournalVoucherLine.account_code,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0)
                - func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(*conds).group_by(JournalVoucherLine.account_code))
    return {code: Decimal(bal) for code, bal in (await db.execute(q)).all()}


async def income_statement(db: AsyncSession, period: str, ytd: bool = True) -> dict:
    coa = await _coa_map(db)
    lo = _year_start(period) if ytd else period
    bals = await _balances_through(db, lo, period)
    revenue, expense = [], []
    rev_total = exp_total = _ZERO
    for code, bal in bals.items():
        acct = coa.get(code)
        if not acct:
            continue
        if acct.account_type == "revenue":
            amt = -bal  # credit-positive
            revenue.append({"account_code": code, "account_name": acct.name, "amount": _s(amt)})
            rev_total += amt
        elif acct.account_type == "expense":
            expense.append({"account_code": code, "account_name": acct.name, "amount": _s(bal)})
            exp_total += bal
    revenue.sort(key=lambda r: r["account_code"])
    expense.sort(key=lambda r: r["account_code"])
    return {
        "period": period, "basis": "ytd" if ytd else "period",
        "revenue": revenue, "expense": expense,
        "revenue_total": _s(rev_total), "expense_total": _s(exp_total),
        "net_income": _s(rev_total - exp_total),
    }


async def balance_sheet(db: AsyncSession, period: str) -> dict:
    coa = await _coa_map(db)
    # cumulative — but only back to the fiscal-year opening voucher, since NC
    # re-states carried-forward balances every year (see crud.fiscal)
    bals = await _balances_through(db, await opening_period(db, period), period)
    assets, liabilities, equity = [], [], []
    a_tot = l_tot = e_tot = ni = _ZERO
    for code, bal in bals.items():
        acct = coa.get(code)
        if not acct:
            continue
        t = acct.account_type
        if t == "asset":
            assets.append({"account_code": code, "account_name": acct.name, "amount": _s(bal)})
            a_tot += bal
        elif t == "liability":
            liabilities.append({"account_code": code, "account_name": acct.name, "amount": _s(-bal)})
            l_tot += -bal
        elif t == "equity":
            equity.append({"account_code": code, "account_name": acct.name, "amount": _s(-bal)})
            e_tot += -bal
        elif t == "revenue":
            ni += -bal
        elif t == "expense":
            ni -= bal
    for sec in (assets, liabilities, equity):
        sec.sort(key=lambda r: r["account_code"])
    equity.append({"account_code": "—", "account_name": "Current Year Earnings (net income)",
                   "amount": _s(ni)})
    e_tot += ni
    return {
        "period": period,
        "assets": assets, "liabilities": liabilities, "equity": equity,
        "assets_total": _s(a_tot),
        "liabilities_total": _s(l_tot), "equity_total": _s(e_tot),
        "liabilities_and_equity_total": _s(l_tot + e_tot),
        "balanced": a_tot == l_tot + e_tot,
    }


# ── opening balances (cut-over migration) ───────────────────────────────────────

async def post_opening_balance(db: AsyncSession, *, as_of: date, lines: list[dict]) -> dict:
    """One balanced `opening` journal. lines: [{account_code, debit?, credit?}].
    Idempotent per as_of date."""
    dr = sum((Decimal(str(l.get("debit", 0))) for l in lines), _ZERO)
    cr = sum((Decimal(str(l.get("credit", 0))) for l in lines), _ZERO)
    if dr != cr:
        raise ValueError(f"Opening entry is unbalanced: debit {dr} != credit {cr}")
    if dr == _ZERO:
        raise ValueError("Opening entry has no amounts")
    coa = await _coa_map(db)
    for l in lines:
        if l.get("account_code") not in coa:
            raise ValueError(f"Unknown account_code {l.get('account_code')!r}")

    doc_id = uuid.uuid5(_NS, f"opening-{as_of.isoformat()}")
    event_id = await emit_event(
        db, source_service="finance", source_doc_type="gl_opening",
        source_doc_id=doc_id, source_doc_number=f"OPEN-{as_of.isoformat()}",
        event_type="opening",
        lines=[{"line_role": "opening_balance", "account_code": l["account_code"],
                "debit": l.get("debit", 0), "credit": l.get("credit", 0)} for l in lines],
        occurred_at=datetime(as_of.year, as_of.month, as_of.day, tzinfo=timezone.utc),
    )
    if event_id is not None:
        from app.crud.journal_voucher import post_system_jv
        await post_system_jv(db, event_id)
    return {"posting_event_id": event_id, "already_posted": event_id is None}


# ── year-end close (the loop) ────────────────────────────────────────────────────

async def close_year(db: AsyncSession, *, fiscal_year: int,
                     retained_earnings_code: str = RETAINED_EARNINGS) -> dict:
    """Sweep revenue/expense balances for the year into Retained Earnings, zeroing
    P&L for the new year. Emits one balanced `closing` journal dated YYYY-12-31.
    Idempotent per fiscal_year."""
    coa = await _coa_map(db)
    if retained_earnings_code not in coa:
        raise ValueError(f"Retained earnings account {retained_earnings_code} not in COA")
    lo, hi = f"{fiscal_year}-01", f"{fiscal_year}-12"
    bals = await _balances_through(db, lo, hi, exclude_closing=True)

    lines, net = [], _ZERO
    for code, bal in sorted(bals.items()):
        acct = coa.get(code)
        if not acct or acct.account_type not in ("revenue", "expense") or bal == _ZERO:
            continue
        # post the opposite to zero the P&L account
        if bal > _ZERO:
            lines.append({"line_role": "close_pl", "account_code": code, "credit": bal})
        else:
            lines.append({"line_role": "close_pl", "account_code": code, "debit": -bal})
        net += -bal  # net income contribution (credit-positive)
    if not lines:
        raise ValueError(f"No revenue/expense activity to close for {fiscal_year}")
    # balancing line to retained earnings (net>0 income → credit RE)
    if net > _ZERO:
        lines.append({"line_role": "close_pl", "account_code": retained_earnings_code, "credit": net})
    else:
        lines.append({"line_role": "close_pl", "account_code": retained_earnings_code, "debit": -net})

    doc_id = uuid.uuid5(_NS, f"close-{fiscal_year}")
    event_id = await emit_event(
        db, source_service="finance", source_doc_type="gl_close",
        source_doc_id=doc_id, source_doc_number=f"CLOSE-{fiscal_year}",
        event_type="closing", lines=lines,
        occurred_at=datetime(fiscal_year, 12, 31, tzinfo=timezone.utc),
    )
    if event_id is not None:
        from app.crud.journal_voucher import post_system_jv
        await post_system_jv(db, event_id)
    return {"fiscal_year": fiscal_year, "posting_event_id": event_id,
            "already_closed": event_id is None, "net_income": _s(net)}
