"""Account balance (科目余额表 能力①) over POSTED journal vouchers.

Additive GL read that aggregates `journal_voucher_lines.local_debit/credit`
(functional currency, CAD) by account + period into opening / period-movement /
closing. Only `posted` vouchers count. Does NOT touch the live gl.py (which still
reads posting_lines) — this is the foundation the account-balance report consumes.
"""
from decimal import Decimal

from sqlalchemy import String, and_, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.fiscal import opening_window, status_filter
from app.models.coa import ChartOfAccount
from app.models.journal_voucher import (
    POSTED,
    JournalVoucher,
    JournalVoucherLine,
    JvLineDimension,
)

_ZERO = Decimal("0")


def _s(v: Decimal) -> str:
    return str(Decimal(v).quantize(Decimal("0.01")))


async def _coa_map(db: AsyncSession) -> dict:
    rows = (await db.execute(select(ChartOfAccount))).scalars().all()
    return {a.code: a for a in rows}


def _children_index(coa_map: dict) -> dict:
    """parent_code -> [child code] (leaves absent). The COA tree link is
    parent_code only — never inferred from code-prefix."""
    idx: dict[str, list[str]] = {}
    for code, acct in coa_map.items():
        if acct.parent_code:
            idx.setdefault(acct.parent_code, []).append(code)
    return idx


def _descendants(children_idx: dict, code: str) -> set:
    """{code} ∪ all transitive children. Cycle-guarded so a self/looping
    parent_code cannot infinite-loop."""
    seen: set[str] = set()
    stack = [code]
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        stack.extend(children_idx.get(c, ()))
    return seen


def _level(coa_map: dict, code: str) -> int:
    """Depth from the tree root (0 = root or orphan). Walks parent_code up,
    cycle-guarded."""
    depth = 0
    seen: set[str] = set()
    cur = code
    while True:
        acct = coa_map.get(cur)
        if acct is None or not acct.parent_code or cur in seen:
            return depth
        seen.add(cur)
        cur = acct.parent_code
        depth += 1


async def _subtree_codes(db: AsyncSession, account_code: str) -> set:
    """{account_code} ∪ all descendant account codes. Leaf/orphan -> {self}."""
    coa = await _coa_map(db)
    return _descendants(_children_index(coa), account_code)


async def account_balance(db: AsyncSession, period: str,
                          include_unposted: bool = False) -> dict:
    """Opening (cumulative posted before `period`) + period movement + closing,
    per account, in local (CAD) amounts. Non-leaf (header) accounts aggregate
    their whole {self ∪ descendants} subtree (NC科目余额表 parity); leaves are
    unchanged. Totals stay from the DIRECT per-line partition so rollup never
    double-counts."""
    coa = await _coa_map(db)
    children = _children_index(coa)

    async def sums(where):
        q = (select(JournalVoucherLine.account_code,
                    func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                    func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
             .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
             .where(status_filter(include_unposted)).where(where)
             .group_by(JournalVoucherLine.account_code))
        return {code: (Decimal(d), Decimal(c)) for code, d, c in (await db.execute(q)).all()}

    opening = await sums(await opening_window(db, period, include_unposted))
    movement = await sums(JournalVoucher.fiscal_period == period)

    active = set(opening) | set(movement)               # codes with a direct line
    all_codes = set(coa) | active
    subtree = {code: _descendants(children, code) for code in all_codes}

    def roll(direct, code):
        d = c = _ZERO
        for s in subtree[code]:
            sd, sc = direct.get(s, (_ZERO, _ZERO))
            d += sd; c += sc
        return d, c

    # show an account iff its subtree contains any direct line (self or descendant)
    shown = [c for c in all_codes if subtree[c] & active]
    shown.sort(key=lambda c: (c is None, c or ""))

    rows = []
    for code in shown:
        od, oc = roll(opening, code)
        md, mc = roll(movement, code)
        open_bal = od - oc
        acct = coa.get(code)
        rows.append({
            "account_code": code or "(unmapped)",
            "account_name": acct.name if acct else "(unmapped)",
            "account_type": acct.account_type if acct else None,
            "is_postable": acct.is_postable if acct else True,
            "level": _level(coa, code) if code else 0,
            "opening": _s(open_bal),
            "period_debit": _s(md), "period_credit": _s(mc),
            "closing": _s(open_bal + md - mc),
        })

    # totals from the DIRECT partition (each line once) — NOT from rolled rows
    tot_dr = tot_cr = tot_close = _ZERO
    for code in active:
        od, oc = opening.get(code, (_ZERO, _ZERO))
        md, mc = movement.get(code, (_ZERO, _ZERO))
        tot_dr += md; tot_cr += mc
        tot_close += (od - oc) + (md - mc)
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
    "6603": "FN",    # 财务费用 Financial expenses
}

# Income-expense items excluded from the per-cost-center detail: finance tracks
# Payroll / Depreciation only at the category level, so they roll into
# category-level tie-out rows instead of being spread over cost centers.
_PAYROLL_PREFIX = "CRM007"
_DEPREC_PREFIX = "CRM004"
#: Shut-down loss — the stop-production reclass that moves a share of MOH into
#: G&A (`Shut down loss - utilities/depreciation/payroll/MOH`). Not budgeted per
#: cost center, same treatment as Payroll and Depreciation (user, 2026-09-16).
_SHUTDOWN_LOSS_PREFIX = "CRM09912"

#: Every income-expense item the per-cost-center dashboard deliberately leaves
#: out, with the name a person would use for it. NOTE CRM09912 has no
#: budget_accounts row today, so it is already dropped by the INNER join in
#: nc_actuals_monthly; listing it here is what keeps it out if the catalog ever
#: gains one — and is what tells JV Validation that its 8.6M CAD is policy, not
#: a defect.
#:
#: This is the SINGLE SOURCE for the exclusion set: services/budget_actual_
#: lineage.py derives the assistant's "what this report leaves out" answer from
#: it rather than restating it, so the explanation cannot drift from the query.
#: The one copy that cannot derive is the frontend's `isExcludedFromDashboard`
#: (epms/src/pages/budget/BudgetDashboard.tsx) — keep it in lockstep by hand.
EXCLUDED_IO_LABELS = {
    _DEPREC_PREFIX: "Depreciation",
    _PAYROLL_PREFIX: "Payroll",
    _SHUTDOWN_LOSS_PREFIX: "Shut-down loss",
}
EXCLUDED_IO_PREFIXES = tuple(EXCLUDED_IO_LABELS)


async def _cc_map(db: AsyncSession) -> dict:
    from app.models.mirrors import CostCenter
    rows = (await db.execute(select(CostCenter))).scalars().all()
    return {c.id: c for c in rows}


def _net(d, c) -> Decimal:
    return Decimal(d) - Decimal(c)


async def _by_cost_center(db: AsyncSession, account_code: str, period: str):
    subtree = await _subtree_codes(db, account_code)
    q = (select(JournalVoucherLine.cost_center_id,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code.in_(subtree))
         .group_by(JournalVoucherLine.cost_center_id))
    return (await db.execute(q)).all()


# ── generic multi-dim expansion (能力② full) ────────────────────────────────────

class BadDims(ValueError):
    """Unknown/unsupported dimension code in a request."""


def _dimensions():
    """dim_code -> (jv_lines column, master model, code attr, name attr).

    supplier/customer share the partner_id column — coa_aux_items keeps them
    apart per account (AP accounts carry suppliers, AR customers). `partner` is
    the union of both: NC's 客商 (BD_ACCASS 0004) does not say which one it is,
    and the voucher sync already folds supplier-or-customer into this very
    column (_resolve_dims), so the union is what the column actually holds.
    Its master is resolved in _resolve_dim, not here — hence the Nones.

    bank_account (NC 银行账户, BD_ACCASS 0011) arrived with migration 0036. It is
    the expansion the bank reconciliation reads: 100201 is one postable account,
    so this dimension is the only thing that separates RBC from Bank of China.
    Rows only exist for vouchers imported since that sync — a `full` nc_sync is
    what backfills history.
    """
    from app.models.mirrors import BudgetAccount, CostCenter, Department, ErpSupplier
    from app.models.nc_bank_account import NcBankAccount
    from app.models.nc_customer import NcCustomer
    return {
        "cost_center": (JournalVoucherLine.cost_center_id, CostCenter, "code", "name"),
        "department": (JournalVoucherLine.department_id, Department, "code", "name"),
        "income_expense_item": (JournalVoucherLine.income_expense_item_id, BudgetAccount, "code", "name"),
        "supplier": (JournalVoucherLine.partner_id, ErpSupplier, "erp_supplier_code", "supplier_name"),
        "customer": (JournalVoucherLine.partner_id, NcCustomer, "code", "name"),
        "partner": (JournalVoucherLine.partner_id, None, None, None),   # union — see _resolve_dim
        "bank_account": (JournalVoucherLine.bank_account_id, NcBankAccount, "code", "name"),
    }


async def _resolve_dim(db: AsyncSession, dim: str, ids: set, reg: dict) -> dict:
    """id -> (code, name) for one dimension.

    `partner` unions suppliers and customers — their ids never collide (separate
    tables, separate UUID pks; measured 0 overlap). Ids in neither master fall
    back to the line's own denormalized partner_name: those parties' master rows
    are gone, and a name beats a blank.
    """
    if not ids:
        return {}
    if dim != "partner":
        _, model, code_attr, name_attr = reg[dim]
        rows = (await db.execute(select(model).where(model.id.in_(ids)))).scalars()
        return {r.id: (getattr(r, code_attr), getattr(r, name_attr)) for r in rows}

    from app.models.mirrors import ErpSupplier
    from app.models.nc_customer import NcCustomer
    out: dict = {}
    for model, code_attr, name_attr in (
            (ErpSupplier, "erp_supplier_code", "supplier_name"),
            (NcCustomer, "code", "name")):
        for r in (await db.execute(select(model).where(model.id.in_(ids)))).scalars():
            out[r.id] = (getattr(r, code_attr), getattr(r, name_attr))
    missing = ids - set(out)
    if missing:
        # Measured in dev data: one orphaned partner_id (no master row on either
        # side) is denormalized under two different partner_name values across
        # jv_lines (e.g. "Jassbhatia Solutions" on one line, "Best Buy" on
        # another) — NC data noise, not a bug here. Without an ORDER BY,
        # .distinct() + setdefault would pick whichever row the DB happened to
        # return first, making the resolved name nondeterministic across runs.
        # Order by (partner_id, partner_name) so the pick is stable — this does
        # not make it "correct", there is no correct name for these ids.
        rows = (await db.execute(
            select(JournalVoucherLine.partner_id, JournalVoucherLine.partner_name)
            .where(JournalVoucherLine.partner_id.in_(missing)).distinct()
            .order_by(JournalVoucherLine.partner_id, JournalVoucherLine.partner_name))).all()
        for pid, pname in rows:
            out.setdefault(pid, (None, pname))
    return out


DIM_LABELS = {
    # expandable (see _dimensions())
    "cost_center": "Cost Center", "department": "Department",
    "income_expense_item": "Income/Expense Item", "supplier": "Supplier",
    "customer": "Customer", "partner": "Partner (Vendor/Customer)",
    "bank_account": "Bank Account",
    # carried from NC BD_ACCASS but not expandable — jv_lines has no column for
    # them (spec §3.4). Listed so "NC configured it, we can't expand it" is visible.
    "employee": "Employee",
    "project": "Project", "project_type": "Project Type",
    "government_grant_project": "Government Grant Project",
    "item": "Item / Material", "item_category": "Item Category",
    "asset_category": "Asset Category", "tax_code": "VAT Tax Code / Rate",
    "bank": "Bank",
    "bank_category": "Bank Category", "country_region": "Country / Region",
    "sales_type": "Sales Type", "credit_card": "Credit Card",
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
                         dims: list[str], include_unposted: bool = False) -> dict:
    """② dynamic expansion, per NC's aux-item balance report: opening (cumulative
    before `period`) + this-period gross debit/credit + closing, grouped by the
    chosen dimension columns. _net is d-c and linear, so children reconcile with
    the parent account row column for column (spec §3)."""
    reg = _check_dims(dims)
    cols = [reg[d][0] for d in dims]
    subtree = await _subtree_codes(db, account_code)

    async def grouped(where):
        q = (select(*cols,
                    func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                    func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
             .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
             .where(status_filter(include_unposted),
                    JournalVoucherLine.account_code.in_(subtree), where)
             .group_by(*cols))
        # key = the dimension-id tuple; value = (debit, credit)
        return {tuple(r[:len(dims)]): (r[len(dims)], r[len(dims) + 1])
                for r in (await db.execute(q)).all()}

    opening = await grouped(await opening_window(db, period, include_unposted))
    movement = await grouped(JournalVoucher.fiscal_period == period)
    # Monthly view (user 2026-07-17): only dimensions that MOVED this period. A
    # dimension with just a carried-forward opening and no current-period line is
    # excluded — otherwise its row shows a balance but the (per-period) Vouchers
    # drill is empty, which reads as broken. Its opening still shows for dims that
    # did move, so an active dimension's opening/closing are intact.
    all_keys = set(movement)

    # batch-load id -> (code, name) per dimension over the union of keys
    lookups: dict[str, dict] = {}
    for i, d in enumerate(dims):
        ids = {k[i] for k in all_keys if k[i] is not None}
        lookups[d] = await _resolve_dim(db, d, ids, reg)

    rows = []
    for key in all_keys:
        od, oc = opening.get(key, (0, 0))
        md, mc = movement.get(key, (0, 0))
        keys = []
        for i, d in enumerate(dims):
            vid = key[i]
            code, name = lookups[d].get(vid, (None, None))
            keys.append({"dim_code": d, "id": str(vid) if vid else None,
                         "code": code, "name": name})
        rows.append({"keys": keys,
                     "opening": _s(_net(od, oc)),
                     "period_debit": _s(md), "period_credit": _s(mc),
                     "closing": _s(_net(od, oc) + _net(md, mc))})
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
    codes = [i.dim_code for i in items]
    # union with the supported registry (user 2026-07-13): NC's per-account config
    # omits cost_center (it enters via voucher aux values, not BD_ACCASS), yet CC
    # is the most-used expansion — configured dims keep their order, registry
    # additions append after.
    codes += [c for c in reg if c not in codes]
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


# ── ④b Budget-vs-Actual grid (predreal) ──────────────────────────────────────

async def _ba_lines(db: AsyncSession, account_code: str, period: str):
    """Posted lines for a category account's subtree in `period`, grouped by
    (cost_center, budget account) with the codes/names joined in. Value = period
    gross DEBIT.

    Keys the budget account the same two ways the dashboard does (see
    `_effective_budget_account`): by account code inside 6603, by income-expense
    item everywhere else. Also returns the RAW NC income-expense code, because
    the policy exclusions have to be recognised by what NC wrote — CRM09912 has
    no budget_accounts row, so matching on the resolved account would miss every
    line of it and leave 1.3M a month sitting in an unnamed "(no cost center)"
    detail row."""
    from sqlalchemy.orm import aliased

    from app.models.mirrors import BudgetAccount, CostCenter
    subtree = await _subtree_codes(db, account_code)
    fn_codes = await _by_account_code_subtree(db)
    by_code = aliased(BudgetAccount)
    acct_id, acct_code = _effective_budget_account(fn_codes, BudgetAccount, by_code)
    acct_name = case((JournalVoucherLine.account_code.in_(fn_codes), by_code.name),
                     else_=BudgetAccount.name)
    dim = aliased(JvLineDimension)
    q = (select(JournalVoucherLine.cost_center_id, acct_id,
                CostCenter.code, CostCenter.name, acct_code, acct_name,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                func.max(dim.value_text))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .outerjoin(CostCenter, JournalVoucherLine.cost_center_id == CostCenter.id)
         .outerjoin(BudgetAccount, JournalVoucherLine.income_expense_item_id == BudgetAccount.id)
         .outerjoin(dim, and_(dim.jv_line_id == JournalVoucherLine.id,
                              dim.dim_code == "income_expense_item")))
    q = _budget_account_join(q, fn_codes, by_code)
    q = (q.where(JournalVoucher.status == POSTED,
                 JournalVoucher.fiscal_period == period,
                 JournalVoucherLine.account_code.in_(subtree))
          .group_by(JournalVoucherLine.cost_center_id, acct_id,
                    CostCenter.code, CostCenter.name, acct_code, acct_name))
    return (await db.execute(q)).all()


async def _ba_unmapped(db: AsyncSession, period: str) -> list:
    """Exceptions panel: posted lines in the 5 predreal categories that resolved
    to NO cost center (account-aware map miss) yet carry an NC cost-center code —
    the lines a human must fix in NC or add to budget_actual_cc_map."""
    from app.models.mirrors import BudgetAccount
    accts: set = set()
    for a in BUDGET_ACTUAL_ACCOUNTS:
        accts |= await _subtree_codes(db, a)
    q = (select(JournalVoucherLine.account_code, JournalVoucherLine.nc_cc_code,
                BudgetAccount.code, BudgetAccount.name,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                func.count())
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .outerjoin(BudgetAccount, JournalVoucherLine.income_expense_item_id == BudgetAccount.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code.in_(accts),
                JournalVoucherLine.cost_center_id.is_(None),
                JournalVoucherLine.nc_cc_code.isnot(None))
         .group_by(JournalVoucherLine.account_code, JournalVoucherLine.nc_cc_code,
                   BudgetAccount.code, BudgetAccount.name))
    return [{"account_code": ac, "nc_cc_code": nc, "income_expense_code": iec,
             "income_expense_name": ien, "actual": _s(Decimal(dr)), "line_count": int(n)}
            for ac, nc, iec, ien, dr, n in (await db.execute(q)).all()]


async def budget_actual_grid(db: AsyncSession, period: str, budget_lookup: dict) -> dict:
    """Budget-vs-Actual grid. Per category (the 5 expense accounts): detail rows
    per (cost center × budget account) with budget/actual/variance, EXCLUDING the
    items that are tracked at category level only — payroll, depreciation and
    shut-down loss — which roll into category-level tie-out rows instead.
    `budget_lookup`: (cost_center_id, budget_account_id) -> Decimal.
    actual = period gross DEBIT (expense accounts net ~0 via 结转).

    The exclusions are recognised off the RAW NC code, not the resolved budget
    account: shut-down loss (CRM09912) has no catalog row, so it used to fall
    through to a detail row with no cost center and no item name — 1,326,701.36
    of it in 2026-08 alone, read as an unexplained hole in G&A."""
    categories = []
    for acct, category in BUDGET_ACTUAL_ACCOUNTS.items():
        detail, total = [], _ZERO
        by_policy: dict[str, Decimal] = {p: _ZERO for p in EXCLUDED_IO_PREFIXES}
        for (cc_id, ie_id, cc_code, cc_name, ie_code, ie_name, dr,
             nc_io_code) in await _ba_lines(db, acct, period):
            dr = Decimal(dr)
            total += dr
            # EITHER source matching is enough. What NC wrote is the only
            # signal for shut-down loss (no catalog row at all); the resolved
            # code is the only one when the dimension row is absent. Where both
            # exist and disagree, excluding is the safe side — a policy item
            # landing in the detail is a wrong number in a named row, while the
            # reverse is a line in a tie-out total that already ties.
            hit = next((p for p in EXCLUDED_IO_PREFIXES
                        if any(c.startswith(p) for c in (nc_io_code, ie_code) if c)),
                       None)
            if hit is not None:
                by_policy[hit] += dr
                continue
            budget = Decimal(budget_lookup.get((cc_id, ie_id), _ZERO))
            detail.append({
                "cost_center_id": str(cc_id) if cc_id else None,
                "cost_center_code": cc_code, "cost_center_name": cc_name,
                "income_expense_item_id": str(ie_id) if ie_id else None,
                "income_expense_code": ie_code, "income_expense_name": ie_name,
                "budget": _s(budget), "actual": _s(dr), "variance": _s(budget - dr),
            })
        # Budget account first: the account code is the axis finance reads down,
        # and the same account's cost centres belong next to each other.
        detail.sort(key=lambda d: (d["income_expense_code"] or "￿",
                                   d["cost_center_code"] or "￿"))
        detail_total = sum((Decimal(d["actual"]) for d in detail), _ZERO)
        policy_total = sum(by_policy.values(), _ZERO)
        categories.append({
            "account_code": acct, "category": category, "detail": detail,
            # Kept as named fields for the existing UI rows, and repeated in
            # `category_level` so a new exclusion shows up without a schema change.
            "payroll_actual": _s(by_policy[_PAYROLL_PREFIX]),
            "depreciation_actual": _s(by_policy[_DEPREC_PREFIX]),
            "category_level": [
                {"prefix": p, "label": EXCLUDED_IO_LABELS[p], "actual": _s(v)}
                for p, v in by_policy.items() if v != _ZERO
            ],
            "detail_actual_total": _s(detail_total),
            "category_actual_total": _s(total),
            "tie_ok": (detail_total + policy_total) == total,
        })
    return {"period": period, "categories": categories,
            "unmapped": await _ba_unmapped(db, period)}


# ── which budget account a line belongs to ───────────────────────────────────
#
# Two different rules, because NC books the two families differently:
#
#   5101 / 5301 / 6601 / 6602   the line carries a 收支项目 (income-expense item)
#                               aux, and that IS the budget account.
#   6603 财务费用                NC does not put an income-expense item on these
#                               lines at all. The ACCOUNT is the budget line —
#                               660301 Interest income, 660303 Bank Charge, …
#                               each exists in budget_accounts under the very
#                               same code (verified: all six codes NC posts to
#                               in 2025-2026 match one).
#
# Before this, 6603 fell through the income-expense join and FN-0103 read zero
# actual for every month of every year while 97,182.57 sat in the books.

#: The category whose budget account is the accounting account itself.
_BY_ACCOUNT_CODE_CATEGORY = "6603"


async def _by_account_code_subtree(db: AsyncSession) -> set:
    return await _subtree_codes(db, _BY_ACCOUNT_CODE_CATEGORY)


def _budget_account_join(q, fn_codes, alias):
    """Attach the by-account-code lookup used for the 6603 subtree."""
    return q.outerjoin(alias, and_(alias.code == JournalVoucherLine.account_code,
                                   JournalVoucherLine.account_code.in_(fn_codes)))


def _effective_account_id_filter(fn_codes, account_id):
    """WHERE clause matching lines that belong to budget account `account_id`
    under either rule: by account code inside the 6603 subtree, by
    income-expense item elsewhere."""
    from app.models.mirrors import BudgetAccount
    in_fn = JournalVoucherLine.account_code.in_(fn_codes)
    by_code = (select(BudgetAccount.code)
               .where(BudgetAccount.id == account_id).scalar_subquery())
    return case(
        (in_fn, JournalVoucherLine.account_code == by_code),
        else_=(JournalVoucherLine.income_expense_item_id == account_id),
    )


def _effective_budget_account(fn_codes, by_item, by_code):
    """(account_id, account_code) for a line: the account-code match inside the
    6603 subtree, the income-expense item everywhere else."""
    in_fn = JournalVoucherLine.account_code.in_(fn_codes)
    return (case((in_fn, by_code.id), else_=by_item.id),
            case((in_fn, by_code.code), else_=by_item.code))


async def nc_actuals_monthly(db: AsyncSession, fiscal_year: int,
                             cost_center_id=None, cc_ids=None) -> dict:
    """NC posted actual per (income_expense_item_id, month) across the 5 predreal
    category subtrees for `fiscal_year`, optionally scoped to one cost center
    (`cost_center_id`) or a set of cost centers (`cc_ids`). Feeds the EPMS Budget
    Dashboard's NC-actual line, keyed by budget account — the line's
    income-expense item, or for the 6603 subtree the account itself (see
    `_effective_budget_account`).
    actual = period gross DEBIT. Returns {account_id: {month:int -> amount:str}}."""
    if cc_ids is not None and len(cc_ids) == 0:
        return {"fiscal_year": fiscal_year, "accounts": {}}
    from sqlalchemy.orm import aliased

    from app.models.mirrors import BudgetAccount
    accts: set = set()
    for a in BUDGET_ACTUAL_ACCOUNTS:
        accts |= await _subtree_codes(db, a)
    fn_codes = await _by_account_code_subtree(db)
    by_code = aliased(BudgetAccount)
    acct_id, acct_code = _effective_budget_account(fn_codes, BudgetAccount, by_code)
    month = func.substr(JournalVoucher.fiscal_period, 6, 2)   # 'MM' -> int in Python
    q = (select(acct_id, month,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .outerjoin(BudgetAccount,
                    JournalVoucherLine.income_expense_item_id == BudgetAccount.id))
    q = _budget_account_join(q, fn_codes, by_code)
    q = (q.where(JournalVoucher.status == POSTED,
                 JournalVoucher.fiscal_period.like(f"{fiscal_year}-%"),
                 JournalVoucherLine.account_code.in_(accts),
                 # A line with no budget account on either rule is not actual
                 # anywhere — it is a JV Validation finding, not a dashboard row.
                 acct_id.isnot(None),
                 # Payroll (CRM007) / Depreciation (CRM004) are category-level
                 # only (finance grid); shut-down loss (CRM09912) is not budgeted
                 # per cost center at all. See EXCLUDED_IO_PREFIXES.
                 *[~acct_code.like(f"{p}%") for p in EXCLUDED_IO_PREFIXES])
          .group_by(acct_id, month))
    if cost_center_id is not None:
        q = q.where(JournalVoucherLine.cost_center_id == cost_center_id)
    elif cc_ids:
        q = q.where(JournalVoucherLine.cost_center_id.in_(cc_ids))
    out: dict = {}
    for aid, mm, dr in (await db.execute(q)).all():
        out.setdefault(str(aid), {})[int(mm)] = _s(Decimal(dr))
    return {"fiscal_year": fiscal_year, "accounts": out}


# ── vendor attribution ───────────────────────────────────────────────────────
#
# NC hangs the 客商/客户 aux on the PAYABLE line, not on the expense line: a
# purchase voucher reads `debit 6602 (no party) / credit 220201 (the vendor)`.
# The dashboard sums debit only — correctly, because a credit is not spend — so
# the vendor never reached the breakdown and 1,394 expense lines carrying
# 2,203,191 CAD of 2026 spend showed as "(no vendor)" on vouchers that name one.
#
# The fix is to take the party from the VOUCHER, not from the credit line (whose
# amount is not actual and must never be added in). It is applied only where it
# is unambiguous: a voucher naming several parties cannot be split across its
# expense lines without inventing an allocation, so those lines are bucketed as
# `multi` and stay visibly unattributed rather than being guessed at.
#
# `source` on each row says where the attribution came from:
#   line    — the expense line named the party itself (unchanged behaviour)
#   voucher — inferred from the voucher's single party
#   multi   — the voucher names several parties; not attributable
#   none    — no party anywhere on the voucher (internal accrual / carry-forward)


def _voucher_party():
    """jv_id -> (distinct party count, the party when there is exactly one)."""
    key = func.coalesce(cast(JournalVoucherLine.partner_id, String),
                        JournalVoucherLine.partner_name)
    return (select(JournalVoucherLine.jv_id.label("jv_id"),
                   func.count(func.distinct(key)).label("n"),
                   func.min(cast(JournalVoucherLine.partner_id, String)).label("pid"),
                   func.min(JournalVoucherLine.partner_name).label("pname"))
            .where(or_(JournalVoucherLine.partner_id.isnot(None),
                       JournalVoucherLine.partner_name.isnot(None)))
            .group_by(JournalVoucherLine.jv_id)
            .subquery())


def _effective_party(vp):
    """(partner_id, partner_name, source) expressions for a line, given the
    voucher-party subquery `vp` already outer-joined on jv_id."""
    own = or_(JournalVoucherLine.partner_id.isnot(None),
              JournalVoucherLine.partner_name.isnot(None))
    pid = case((own, cast(JournalVoucherLine.partner_id, String)),
               (vp.c.n == 1, vp.c.pid))
    pname = case((own, JournalVoucherLine.partner_name),
                 (vp.c.n == 1, vp.c.pname))
    source = case((own, literal("line")),
                  (vp.c.n == 1, literal("voucher")),
                  (vp.c.n > 1, literal("multi")),
                  else_=literal("none"))
    return pid, pname, source


def _party_key(pid, pname, source) -> str:
    """Grouping identity for a partner row. Keyed on the party itself so a
    line-named and a voucher-inferred hit on the SAME vendor land in one row;
    falls back to the bucket name when there is no party.

    Note this also fixes a second defect: the previous key was `partner_id or
    "__none__"`, so every line whose vendor exists in NC but has no UniOps
    vendor record (partner_id NULL, partner_name = the raw NC code) collapsed
    into one bucket that then displayed whichever name it saw first."""
    return pid or pname or source


async def nc_actuals_by_cost_center(db: AsyncSession, fiscal_year: int,
                                   through_month: int = 12,
                                   cc_ids=None) -> dict:
    """NC posted actual per cost centre for a year, up to and including a month.

    The dashboard's figure summed a different way. Someone comparing a year's
    budget with what has been spent so far asks for it per cost centre, and the
    only aggregate that existed was per budget account — so the question could
    not be answered at all, and the assistant returned the plan alone.

    Every filter is the dashboard's, deliberately: same five category subtrees,
    same posted-only rule, same gross debit, same exclusions, same budget-account
    keying. A total assembled from a different basis would not be comparable
    with the plan it is about to be subtracted from, and nothing on the screen
    would show that.

    `dropped` is the other half of the answer. Lines that carry an expense
    account but no cost centre, or none of the two budget-account keys, are not
    in any cell of the report — they are JV Validation findings. Reporting the
    total without saying how much is missing from it is how a comparison
    quietly understates spending.
    """
    if cc_ids is not None and len(cc_ids) == 0:
        return {"fiscal_year": fiscal_year, "through_month": through_month,
                "cost_centers": [], "dropped": {}}
    from sqlalchemy.orm import aliased

    from app.models.mirrors import BudgetAccount, CostCenter
    accts: set = set()
    for a in BUDGET_ACTUAL_ACCOUNTS:
        accts |= await _subtree_codes(db, a)
    fn_codes = await _by_account_code_subtree(db)
    by_code = aliased(BudgetAccount)
    acct_id, acct_code = _effective_budget_account(fn_codes, BudgetAccount, by_code)
    month = func.substr(JournalVoucher.fiscal_period, 6, 2)

    def _base(select_cols):
        q = (select(*select_cols)
             .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
             .outerjoin(BudgetAccount,
                        JournalVoucherLine.income_expense_item_id == BudgetAccount.id))
        q = _budget_account_join(q, fn_codes, by_code)
        return q.where(JournalVoucher.status == POSTED,
                       JournalVoucher.fiscal_period.like(f"{fiscal_year}-%"),
                       # 'MM' is zero-padded, so comparing it as text is the
                       # same order as comparing it as a number — and saves a
                       # cast on every line of a 325k-row table.
                       month <= f"{through_month:02d}",
                       JournalVoucherLine.account_code.in_(accts))

    kept = [acct_id.isnot(None), JournalVoucherLine.cost_center_id.isnot(None),
            *[~acct_code.like(f"{p}%") for p in EXCLUDED_IO_PREFIXES]]

    q = (_base([JournalVoucherLine.cost_center_id, CostCenter.code, CostCenter.name,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0)])
         .outerjoin(CostCenter, JournalVoucherLine.cost_center_id == CostCenter.id)
         .where(*kept)
         .group_by(JournalVoucherLine.cost_center_id, CostCenter.code, CostCenter.name))
    if cc_ids:
        q = q.where(JournalVoucherLine.cost_center_id.in_(cc_ids))

    rows = [{"cost_center_id": str(cid), "cost_center_code": code,
             "cost_center_name": name, "actual": _s(Decimal(dr))}
            for cid, code, name, dr in (await db.execute(q)).all()]
    rows.sort(key=lambda r: r["cost_center_code"] or "\uffff")

    # What the same window holds that no cell of the report can show. Counted
    # over the whole company even when the caller is scoped to a department:
    # an unattributable line has no cost centre to scope it by, which is the
    # whole reason it is missing.
    async def _missing(*conds):
        got = (await db.execute(_base([
            func.count(), func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
        ]).where(*conds))).one()
        return {"lines": int(got[0]), "amount": _s(Decimal(got[1]))}

    return {
        "fiscal_year": fiscal_year,
        "through_month": through_month,
        "cost_centers": rows,
        # Disjoint on purpose, and in this order: a payroll line with no cost
        # centre belongs to the policy bucket, not to both. Overlapping buckets
        # are worse than no buckets — the first thing anyone does with three
        # numbers under one heading is add them up, and the first version of
        # this double-counted 906,456.65 that way.
        "dropped": {
            "excluded_items": await _missing(
                acct_id.isnot(None),
                or_(*[acct_code.like(f"{p}%") for p in EXCLUDED_IO_PREFIXES])),
            "no_budget_account": await _missing(acct_id.is_(None)),
            "no_cost_center": await _missing(
                acct_id.isnot(None),
                *[~acct_code.like(f"{p}%") for p in EXCLUDED_IO_PREFIXES],
                JournalVoucherLine.cost_center_id.is_(None)),
        },
        "dropped_means": (
            "Not in this total and not in any cell of the Budget Dashboard. "
            "The three buckets do not overlap, so they can be added up. "
            "no_cost_center and no_budget_account are JV Validation findings — "
            "postings that resolved to nothing to hang them on. excluded_items "
            "is by design: "
            + ", ".join(f"{label} ({prefix})"
                        for prefix, label in EXCLUDED_IO_LABELS.items())
            + " are not budgeted per cost centre."
        ),
    }


async def _predreal_subtree(db: AsyncSession) -> set:
    accts: set = set()
    for a in BUDGET_ACTUAL_ACCOUNTS:
        accts |= await _subtree_codes(db, a)
    return accts


async def nc_partner_monthly(db: AsyncSession, income_expense_item_id, fiscal_year: int,
                             cost_center_id=None, cc_ids=None) -> dict:
    """Budget Dashboard drill: for ONE budget account (收支项目) — with cost center
    already locked by the caller — NC posted actual per partner (客商/供应商/客户)
    per month across the fiscal year. Rows = partners that appeared that year,
    sorted by year total desc; cells = monthly gross debit. The party is the
    line's own when it has one, else the voucher's when the voucher names
    exactly one — see `_voucher_party`. Each row carries `key` (the identity to
    pass back for the drill), `source`, and `inferred_total` (how much of the
    row came from the voucher rather than the line)."""
    if cc_ids is not None and len(cc_ids) == 0:
        return {"fiscal_year": fiscal_year,
                "income_expense_item_id": str(income_expense_item_id),
                "cost_center_id": str(cost_center_id) if cost_center_id else None,
                "partners": []}
    accts = await _predreal_subtree(db)
    month = func.substr(JournalVoucher.fiscal_period, 6, 2)
    vp = _voucher_party()
    pid_e, pname_e, source_e = _effective_party(vp)
    # Match the dashboard's own keying: inside 6603 the budget account is the
    # accounting account, so filtering on income_expense_item_id alone would
    # drill into an empty list for every FN-0103 cell.
    fn_codes = await _by_account_code_subtree(db)
    acct_id = _effective_account_id_filter(fn_codes, income_expense_item_id)
    q = (select(pid_e, pname_e, source_e, month,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .outerjoin(vp, vp.c.jv_id == JournalVoucherLine.jv_id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period.like(f"{fiscal_year}-%"),
                JournalVoucherLine.account_code.in_(accts),
                acct_id,
                # actual = debit only; drop pure-credit carry-forward lines (and
                # thus credit-only partners) so the breakdown isn't cluttered.
                JournalVoucherLine.local_debit != 0))
    if cost_center_id is not None:
        q = q.where(JournalVoucherLine.cost_center_id == cost_center_id)
    elif cc_ids:
        q = q.where(JournalVoucherLine.cost_center_id.in_(cc_ids))
    q = q.group_by(pid_e, pname_e, source_e, month)

    agg: dict = {}
    for pid, pname, source, mm, dr in (await db.execute(q)).all():
        key = _party_key(pid, pname, source)
        rec = agg.setdefault(key, {"key": key, "partner_id": pid,
                                   "partner_name": pname, "source": source,
                                   "by_month": {}, "_total": _ZERO,
                                   "_inferred": _ZERO})
        d = Decimal(dr)
        rec["by_month"][int(mm)] = _s(Decimal(rec["by_month"].get(int(mm), 0)) + d)
        rec["_total"] += d
        if source == "voucher":
            rec["_inferred"] += d
            # A vendor seen both ways is one row; label it by the weaker source
            # only if EVERY hit was inferred.
            if rec["source"] == "line":
                rec["source"] = "mixed"
        elif source == "line" and rec["source"] == "voucher":
            rec["source"] = "mixed"
        if pname and not rec["partner_name"]:
            rec["partner_name"] = pname
    partners = sorted(agg.values(), key=lambda r: r["_total"], reverse=True)
    for r in partners:
        r["year_total"] = _s(r.pop("_total"))
        r["inferred_total"] = _s(r.pop("_inferred"))
    return {"fiscal_year": fiscal_year,
            "income_expense_item_id": str(income_expense_item_id),
            "cost_center_id": str(cost_center_id) if cost_center_id else None,
            "partners": partners}


async def nc_partner_monthly_all(db: AsyncSession, *, fiscal_year: int,
                                 cost_center_id=None, cc_ids=None) -> dict:
    """Bulk vendor (客商) breakdown for ALL predreal budget accounts in ONE query —
    the export equivalent of calling nc_partner_monthly per account. Same party
    attribution as nc_partner_monthly (line's own party, else the voucher's when
    unambiguous — see `_voucher_party`). Returns {account_id_str: [ {key,
    partner_id, partner_name, source, by_month{month:str}, year_total,
    inferred_total} ... sorted by year_total desc ]}."""
    if cc_ids is not None and len(cc_ids) == 0:
        return {}
    from sqlalchemy.orm import aliased

    from app.models.mirrors import BudgetAccount
    accts = await _predreal_subtree(db)
    month = func.substr(JournalVoucher.fiscal_period, 6, 2)
    vp = _voucher_party()
    pid_e, pname_e, source_e = _effective_party(vp)
    fn_codes = await _by_account_code_subtree(db)
    by_code = aliased(BudgetAccount)
    acct_id, _ = _effective_budget_account(fn_codes, BudgetAccount, by_code)
    q = (select(acct_id, pid_e, pname_e, source_e, month,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .outerjoin(BudgetAccount,
                    JournalVoucherLine.income_expense_item_id == BudgetAccount.id)
         .outerjoin(vp, vp.c.jv_id == JournalVoucherLine.jv_id))
    q = _budget_account_join(q, fn_codes, by_code)
    q = q.where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period.like(f"{fiscal_year}-%"),
                JournalVoucherLine.account_code.in_(accts),
                JournalVoucherLine.local_debit != 0)
    if cost_center_id is not None:
        q = q.where(JournalVoucherLine.cost_center_id == cost_center_id)
    elif cc_ids:
        q = q.where(JournalVoucherLine.cost_center_id.in_(cc_ids))
    q = q.group_by(acct_id, pid_e, pname_e, source_e, month)

    by_acct: dict = {}
    for aid, pid, pname, source, mm, dr in (await db.execute(q)).all():
        if aid is None:
            continue
        agg = by_acct.setdefault(str(aid), {})
        key = _party_key(pid, pname, source)
        rec = agg.setdefault(key, {"key": key, "partner_id": pid,
                                   "partner_name": pname, "source": source,
                                   "by_month": {}, "_total": _ZERO,
                                   "_inferred": _ZERO})
        d = Decimal(dr)
        rec["by_month"][int(mm)] = _s(Decimal(rec["by_month"].get(int(mm), 0)) + d)
        rec["_total"] += d
        if source == "voucher":
            rec["_inferred"] += d
            if rec["source"] == "line":
                rec["source"] = "mixed"
        elif source == "line" and rec["source"] == "voucher":
            rec["source"] = "mixed"
        if pname and not rec["partner_name"]:
            rec["partner_name"] = pname

    out: dict = {}
    for aid, agg in by_acct.items():
        partners = sorted(agg.values(), key=lambda r: r["_total"], reverse=True)
        for r in partners:
            r["year_total"] = _s(r.pop("_total"))
            r["inferred_total"] = _s(r.pop("_inferred"))
        out[aid] = partners
    return out


async def nc_partner_vouchers(db: AsyncSession, income_expense_item_id, fiscal_year: int,
                              month: int, cost_center_id=None, partner_id=None,
                              cc_ids=None) -> dict:
    """Drill for one (budget account × cost center × party × month): the posted
    JV lines behind it.

    `partner_id` is the `key` from the partner breakdown, so the filter matches
    exactly what the row summed: a vendor uuid, the raw NC code for a vendor with
    no UniOps record, or one of the bucket names 'multi' / 'none'. It is compared
    against the EFFECTIVE party (line's own, else the voucher's single party) —
    filtering on the line's own party alone would return nothing for every row
    the voucher fallback created."""
    from app.models.coa import ChartOfAccount
    period = f"{fiscal_year}-{int(month):02d}"
    if cc_ids is not None and len(cc_ids) == 0:
        return {"period": period, "rows": []}
    accts = await _predreal_subtree(db)
    coa = {a.code: a for a in (await db.execute(select(ChartOfAccount))).scalars()}
    vp = _voucher_party()
    pid_e, pname_e, source_e = _effective_party(vp)
    fn_codes = await _by_account_code_subtree(db)
    acct_id = _effective_account_id_filter(fn_codes, income_expense_item_id)
    q = (select(JournalVoucherLine, JournalVoucher, pid_e, pname_e, source_e)
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .outerjoin(vp, vp.c.jv_id == JournalVoucherLine.jv_id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code.in_(accts),
                acct_id,
                JournalVoucherLine.local_debit != 0)   # debit-only (drop carry-forward credit)
         .order_by(JournalVoucher.voucher_date))
    if cost_center_id is not None:
        q = q.where(JournalVoucherLine.cost_center_id == cost_center_id)
    elif cc_ids:
        q = q.where(JournalVoucherLine.cost_center_id.in_(cc_ids))
    if partner_id is not None:
        key = str(partner_id)
        q = q.where(func.coalesce(pid_e, pname_e, source_e) == key)
    rows = []
    for ln, jv, pid, pname, source in (await db.execute(q)).all():
        acct = coa.get(ln.account_code)
        rows.append({
            "jv_id": str(jv.id), "jv_number": jv.jv_number,
            "voucher_date": jv.voucher_date.isoformat(),
            "account_code": ln.account_code, "account_name": acct.name if acct else None,
            "summary": ln.summary or jv.summary,
            # The party actually attributed to this line, plus where it came
            # from — a reader who sees a vendor on an expense line that does not
            # carry one in NC needs to know it came off the voucher.
            "partner_name": pname,
            "partner_source": source,
            "line_partner_name": ln.partner_name,
            "local_debit": str(ln.local_debit), "local_credit": str(ln.local_credit),
        })
    return {"period": period, "rows": rows}


async def account_vouchers(db: AsyncSession, account_code: str, period: str,
                           dims_values: dict | None = None,
                           include_unposted: bool = False) -> dict:
    """③ drill-down: posted JV lines for an account (rolled over its
    {self ∪ descendants} subtree), optionally filtered by a dimension-value
    combo ({dim_code: uuid | None}; None = IS NULL). Each row carries its own
    account_code/name so a header drill shows which child a line belongs to.
    Each row carries `posted` so a drill taken with `include_unposted` shows
    which lines are only entered — otherwise the extra rows look like the report
    disagreeing with itself."""
    subtree = await _subtree_codes(db, account_code)
    coa = await _coa_map(db)
    q = (select(JournalVoucherLine, JournalVoucher)
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(status_filter(include_unposted),
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code.in_(subtree))
         .order_by(JournalVoucher.voucher_date))
    if dims_values:
        reg = _check_dims(list(dims_values.keys()))
        for d, v in dims_values.items():
            col = reg[d][0]
            q = q.where(col.is_(None) if v is None else col == v)
    rows = []
    for ln, jv in (await db.execute(q)).all():
        acct = coa.get(ln.account_code)
        rows.append({
            "jv_id": str(jv.id), "jv_number": jv.jv_number,
            "voucher_date": jv.voucher_date.isoformat(),
            "account_code": ln.account_code,
            "account_name": acct.name if acct else None,
            "summary": ln.summary or jv.summary,
            "local_debit": str(ln.local_debit), "local_credit": str(ln.local_credit),
            "posted": jv.status == POSTED,
            "cost_center_id": str(ln.cost_center_id) if ln.cost_center_id else None,
            "source_doc_type": jv.source_doc_type,
            "source_doc_id": str(jv.source_doc_id) if jv.source_doc_id else None,
            "source_doc_number": jv.source_doc_number,
        })
    return {"account_code": account_code, "period": period, "rows": rows}
