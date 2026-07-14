"""Account balance (科目余额表 能力①) over POSTED journal vouchers.

Additive GL read that aggregates `journal_voucher_lines.local_debit/credit`
(functional currency, CAD) by account + period into opening / period-movement /
closing. Only `posted` vouchers count. Does NOT touch the live gl.py (which still
reads posting_lines) — this is the foundation the account-balance report consumes.
"""
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coa import ChartOfAccount
from app.models.journal_voucher import POSTED, JournalVoucher, JournalVoucherLine

_ZERO = Decimal("0")


def _s(v: Decimal) -> str:
    return str(Decimal(v).quantize(Decimal("0.01")))


async def _coa_map(db: AsyncSession) -> dict:
    rows = (await db.execute(select(ChartOfAccount))).scalars().all()
    return {a.code: a for a in rows}


async def account_balance(db: AsyncSession, period: str) -> dict:
    """Opening (cumulative posted before `period`) + period movement + closing,
    per account, in local (CAD) amounts. Mirrors gl.trial_balance's shape but
    sourced from posted JV lines."""
    coa = await _coa_map(db)

    async def sums(where):
        q = (select(JournalVoucherLine.account_code,
                    func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                    func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
             .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
             .where(JournalVoucher.status == POSTED).where(where)
             .group_by(JournalVoucherLine.account_code))
        return {code: (Decimal(d), Decimal(c)) for code, d, c in (await db.execute(q)).all()}

    opening = await sums(JournalVoucher.fiscal_period < period)
    movement = await sums(JournalVoucher.fiscal_period == period)

    codes = sorted(set(opening) | set(movement), key=lambda c: (c is None, c or ""))
    rows = []
    tot_dr = tot_cr = tot_close = _ZERO
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
        tot_dr += md; tot_cr += mc; tot_close += close_bal
    return {
        "period": period, "rows": rows,
        "totals": {"period_debit": _s(tot_dr), "period_credit": _s(tot_cr),
                   "closing": _s(tot_close)},
        "balanced": tot_dr == tot_cr and tot_close == _ZERO,
    }


# ── ② auxiliary expansion (by cost center) + ③ voucher drill-down + ④ Budget Actual ──

# Budget Actual: expense account -> category; cost-center codes carry the matching
# prefix (5101/MOH*, 5301/RD*, 6601/SELL*, 6602/GA*). Config, not hardcoded logic.
BUDGET_ACTUAL_ACCOUNTS = {
    "5101": "MOH",   # 制造费用 Manufacturing Overhead
    "5301": "RD",    # 研发费用 R&D
    "6601": "SELL",  # 销售费用 Selling
    "6602": "GA",    # 管理费用 G&A
}


async def _cc_map(db: AsyncSession) -> dict:
    from app.models.mirrors import CostCenter
    rows = (await db.execute(select(CostCenter))).scalars().all()
    return {c.id: c for c in rows}


def _net(d, c) -> Decimal:
    return Decimal(d) - Decimal(c)


async def _by_cost_center(db: AsyncSession, account_code: str, period: str):
    q = (select(JournalVoucherLine.cost_center_id,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code == account_code)
         .group_by(JournalVoucherLine.cost_center_id))
    return (await db.execute(q)).all()


# ── generic multi-dim expansion (能力② full) ────────────────────────────────────

class BadDims(ValueError):
    """Unknown/unsupported dimension code in a request."""


def _dimensions():
    """dim_code -> (jv_lines column, mirror model). Registry — adding a future
    dimension (supplier/customer) is one line here once its column+mirror exist."""
    from app.models.mirrors import BudgetAccount, CostCenter, Department
    return {
        "cost_center": (JournalVoucherLine.cost_center_id, CostCenter),
        "department": (JournalVoucherLine.department_id, Department),
        "income_expense_item": (JournalVoucherLine.income_expense_item_id, BudgetAccount),
    }


DIM_LABELS = {
    "cost_center": "Cost Center", "department": "Department",
    "income_expense_item": "Income/Expense Item", "supplier": "Supplier",
    "customer": "Customer", "employee": "Employee", "project": "Project",
}


def _check_dims(dims: list[str]) -> dict:
    reg = _dimensions()
    bad = [d for d in dims if d not in reg]
    if bad or not dims:
        raise BadDims(f"unknown or empty dims: {bad or dims}")
    if len(dims) != len(set(dims)):
        raise BadDims(f"duplicate dims: {dims}")
    return reg


async def expand_by_dims(db: AsyncSession, account_code: str, period: str,
                         dims: list[str]) -> dict:
    """② dynamic expansion: GROUP BY the chosen dimension columns (all promoted
    columns — no KV join), resolve each id to code/name via its mirror."""
    reg = _check_dims(dims)
    cols = [reg[d][0] for d in dims]
    q = (select(*cols,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code == account_code)
         .group_by(*cols))
    raw = (await db.execute(q)).all()

    # batch-load mirror rows per dimension
    lookups: dict[str, dict] = {}
    for i, d in enumerate(dims):
        ids = {row[i] for row in raw if row[i] is not None}
        model = reg[d][1]
        lookups[d] = ({r.id: r for r in (await db.execute(
            select(model).where(model.id.in_(ids)))).scalars()} if ids else {})

    rows = []
    for row in raw:
        keys = []
        for i, d in enumerate(dims):
            vid = row[i]
            m = lookups[d].get(vid)
            keys.append({"dim_code": d, "id": str(vid) if vid else None,
                         "code": m.code if m else None, "name": m.name if m else None})
        rows.append({"keys": keys, "amount": _s(_net(row[len(dims)], row[len(dims) + 1]))})
    rows.sort(key=lambda r: tuple(k["code"] or "￿" for k in r["keys"]))
    return {"account_code": account_code, "period": period, "dims": dims, "rows": rows}


async def list_dims(db: AsyncSession, account_code: str) -> dict:
    """Dims checkable for an account: coa_aux_items config (NC BD_ACCASS import),
    falling back to the full supported registry for unconfigured accounts."""
    from app.models.coa import CoaAuxItem
    reg = _dimensions()
    items = (await db.execute(
        select(CoaAuxItem).where(CoaAuxItem.account_code == account_code)
        .order_by(CoaAuxItem.seq))).scalars().all()
    codes = [i.dim_code for i in items] if items else list(reg.keys())
    return {"account_code": account_code, "dims": [
        {"dim_code": c, "label": DIM_LABELS.get(c, c), "supported": c in reg}
        for c in codes]}


async def budget_actual(db: AsyncSession, period: str) -> dict:
    """④ Budget Actual: the 4 expense accounts' actuals per cost center, category
    tagged by account (5101→MOH / 5301→RD / 6601→SELL / 6602→GA)."""
    cc = await _cc_map(db)
    rows = []
    for acct, category in BUDGET_ACTUAL_ACCOUNTS.items():
        for ccid, d, c in await _by_cost_center(db, acct, period):
            center = cc.get(ccid)
            rows.append({
                "account_code": acct, "category": category,
                "cost_center_id": str(ccid) if ccid else None,
                "cost_center_code": center.code if center else None,
                "cost_center_name": center.name if center else None,
                # Actual spend = period DEBIT movement. Expense accounts net to ~0
                # within a period (结转/allocated out), so net would understate.
                "actual": _s(Decimal(d)),
            })
    return {"period": period, "rows": rows}


async def account_vouchers(db: AsyncSession, account_code: str, period: str,
                           dims_values: dict | None = None) -> dict:
    """③ drill-down: posted JV lines for an account, optionally filtered by a
    dimension-value combo ({dim_code: uuid | None}; None = IS NULL)."""
    q = (select(JournalVoucherLine, JournalVoucher)
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code == account_code)
         .order_by(JournalVoucher.voucher_date))
    if dims_values:
        reg = _check_dims(list(dims_values.keys()))
        for d, v in dims_values.items():
            col = reg[d][0]
            q = q.where(col.is_(None) if v is None else col == v)
    rows = []
    for ln, jv in (await db.execute(q)).all():
        rows.append({
            "jv_id": str(jv.id), "jv_number": jv.jv_number,
            "voucher_date": jv.voucher_date.isoformat(),
            "summary": ln.summary or jv.summary,
            "local_debit": str(ln.local_debit), "local_credit": str(ln.local_credit),
            "cost_center_id": str(ln.cost_center_id) if ln.cost_center_id else None,
            "source_doc_type": jv.source_doc_type,
            "source_doc_id": str(jv.source_doc_id) if jv.source_doc_id else None,
            "source_doc_number": jv.source_doc_number,
        })
    return {"account_code": account_code, "period": period, "rows": rows}
