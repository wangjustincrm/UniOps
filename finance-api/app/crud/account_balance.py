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


async def account_balance(db: AsyncSession, period: str) -> dict:
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
             .where(JournalVoucher.status == POSTED).where(where)
             .group_by(JournalVoucherLine.account_code))
        return {code: (Decimal(d), Decimal(c)) for code, d, c in (await db.execute(q)).all()}

    opening = await sums(JournalVoucher.fiscal_period < period)
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
}


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
    """
    from app.models.mirrors import BudgetAccount, CostCenter, Department, ErpSupplier
    from app.models.nc_customer import NcCustomer
    return {
        "cost_center": (JournalVoucherLine.cost_center_id, CostCenter, "code", "name"),
        "department": (JournalVoucherLine.department_id, Department, "code", "name"),
        "income_expense_item": (JournalVoucherLine.income_expense_item_id, BudgetAccount, "code", "name"),
        "supplier": (JournalVoucherLine.partner_id, ErpSupplier, "erp_supplier_code", "supplier_name"),
        "customer": (JournalVoucherLine.partner_id, NcCustomer, "code", "name"),
        "partner": (JournalVoucherLine.partner_id, None, None, None),   # union — see _resolve_dim
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
    # carried from NC BD_ACCASS but not expandable — jv_lines has no column for
    # them (spec §3.4). Listed so "NC configured it, we can't expand it" is visible.
    "employee": "Employee",
    "project": "Project", "project_type": "Project Type",
    "government_grant_project": "Government Grant Project",
    "item": "Item / Material", "item_category": "Item Category",
    "asset_category": "Asset Category", "tax_code": "VAT Tax Code / Rate",
    "bank": "Bank", "bank_account": "Bank Account",
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
                         dims: list[str]) -> dict:
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
             .where(JournalVoucher.status == POSTED,
                    JournalVoucherLine.account_code.in_(subtree), where)
             .group_by(*cols))
        # key = the dimension-id tuple; value = (debit, credit)
        return {tuple(r[:len(dims)]): (r[len(dims)], r[len(dims) + 1])
                for r in (await db.execute(q)).all()}

    opening = await grouped(JournalVoucher.fiscal_period < period)
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


async def account_vouchers(db: AsyncSession, account_code: str, period: str,
                           dims_values: dict | None = None) -> dict:
    """③ drill-down: posted JV lines for an account (rolled over its
    {self ∪ descendants} subtree), optionally filtered by a dimension-value
    combo ({dim_code: uuid | None}; None = IS NULL). Each row carries its own
    account_code/name so a header drill shows which child a line belongs to."""
    subtree = await _subtree_codes(db, account_code)
    coa = await _coa_map(db)
    q = (select(JournalVoucherLine, JournalVoucher)
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(JournalVoucher.status == POSTED,
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
            "cost_center_id": str(ln.cost_center_id) if ln.cost_center_id else None,
            "source_doc_type": jv.source_doc_type,
            "source_doc_id": str(jv.source_doc_id) if jv.source_doc_id else None,
            "source_doc_number": jv.source_doc_number,
        })
    return {"account_code": account_code, "period": period, "rows": rows}
