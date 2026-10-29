"""Voucher lines with their auxiliaries named — the JV Detail page, its CSV
export, and the Aux. Acctg column of the voucher detail modal.

The NC sync stores each auxiliary as its CODE only (jv_line_dimensions.value_text).
NC's own voucher screen prints 【部门：0106/Engineering】, so the name is added
here at read time from the masters UniOps already holds. A code with no local
master (cost centre, project) shows as the code alone rather than a guess.
"""
import csv
import io
import re
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.list_sort import lower, order_by
from app.models.coa import ChartOfAccount
from app.models.journal_voucher import JournalVoucher, JournalVoucherLine, JvLineDimension
from app.models.mirrors import BudgetAccount, Department, Material
from app.models.nc_bank_account import NcBankAccount
from app.models.nc_customer import NcCustomer

# Column order for the auxiliaries: NC's own Aux. Acctg order for the common
# ones (部门, 收支项目, 成本中心, 项目), then the parties, then the rest. A code
# not listed sorts after these, alphabetically.
DIM_ORDER = (
    "department", "income_expense_item", "cost_center", "project",
    "supplier", "customer", "partner", "employee",
    "item", "item_category", "bank_account",
)
DIM_LABELS = {
    "department": "Department", "cost_center": "Cost Center",
    "income_expense_item": "Income/Expense Item", "supplier": "Supplier",
    "customer": "Customer", "partner": "Partner", "bank_account": "Bank Account",
    "item": "Item", "item_category": "Item Category", "project": "Project",
    "employee": "Employee",
}
# Party auxiliaries: the sync already resolved the party's name onto the line
# (partner_name). `partner` (客商) keeps NC's raw value pk as its code, which
# means nothing to a reader, so it shows the name alone.
_PARTY_DIMS = ("supplier", "customer", "partner")


def dim_sort_key(code: str):
    return (DIM_ORDER.index(code), "") if code in DIM_ORDER else (len(DIM_ORDER), code)


def dim_label(code: str) -> str:
    return DIM_LABELS.get(code, code)


class AuxNames:
    """code -> name per auxiliary, from the local masters. Whole tables, loaded
    once: the largest (materials) is a few thousand rows."""

    def __init__(self, maps: dict[str, dict[str, str]]):
        self.maps = maps

    @classmethod
    async def load(cls, db: AsyncSession) -> "AuxNames":
        async def _pairs(stmt):
            return {c: n for c, n in (await db.execute(stmt)).all() if c and n}

        banks: dict[str, str] = {}
        for code, acc_num, name in (await db.execute(
                select(NcBankAccount.code, NcBankAccount.acc_num, NcBankAccount.name))).all():
            # The bank auxiliary carries the account code, or the number when NC
            # fell back to it; either finds the same label.
            for k in (code, acc_num):
                if k and name:
                    banks.setdefault(k, name)
        return cls({
            "department": await _pairs(select(Department.code, Department.name)),
            "income_expense_item": await _pairs(select(BudgetAccount.code, BudgetAccount.name)),
            "item": await _pairs(select(Material.code, Material.name)),
            "customer": await _pairs(select(NcCustomer.code, NcCustomer.name)),
            "bank_account": banks,
        })

    def display(self, dim_code: str, value: str | None, partner_name: str | None) -> str:
        """'code/name' as NC prints it; the code alone when no name is known."""
        value = (value or "").strip()
        if dim_code == "partner":
            return partner_name or value
        name = self.maps.get(dim_code, {}).get(value)
        if name is None and dim_code in _PARTY_DIMS:
            name = partner_name
        if value and name:
            return f"{value}/{name}"
        return value or (name or "")


async def dims_for(db: AsyncSession, line_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[tuple[str, str | None]]]:
    """line id -> [(dim_code, value_text)], in DIM_ORDER."""
    out: dict[uuid.UUID, list[tuple[str, str | None]]] = {}
    for i in range(0, len(line_ids), 5000):
        for lid, code, val in (await db.execute(
                select(JvLineDimension.jv_line_id, JvLineDimension.dim_code,
                       JvLineDimension.value_text)
                .where(JvLineDimension.jv_line_id.in_(line_ids[i:i + 5000])))).all():
            out.setdefault(lid, []).append((code, val))
    for v in out.values():
        v.sort(key=lambda d: dim_sort_key(d[0]))
    return out


async def book_dim_codes(db: AsyncSession) -> list[str]:
    """Auxiliaries this book uses — the JV Detail columns. Fixed for the book
    rather than per page, so columns do not come and go while paging."""
    codes = (await db.execute(select(JvLineDimension.dim_code).distinct())).scalars().all()
    return sorted(codes, key=dim_sort_key)


@dataclass
class LineFilters:
    period_from: str | None = None
    period_to: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    status: str | None = None
    source_subsystem: str | None = None
    account_code: str | None = None
    currency: str | None = None
    aux_code: str | None = None
    aux_value: str | None = None
    q: str | None = None
    # Click-to-sort (the shared sort + dir contract); None = the default order.
    sort: str | None = None
    dir: str = "asc"


# Sortable columns of the JV Detail list, by the key the page sends. The
# whitelist is the SQL boundary (app.core.list_sort). Amounts sort with 0 as
# empty: the page shows a credit line's debit as blank, so blanks go last.
_SORTABLE = {
    "voucher_date": JournalVoucher.voucher_date,
    "jv_number": JournalVoucher.jv_number,
    "line_no": JournalVoucherLine.line_no,
    "account_code": JournalVoucherLine.account_code,
    "summary": lower(JournalVoucherLine.summary),
    "currency": JournalVoucherLine.currency,
    "local_debit": func.nullif(JournalVoucherLine.local_debit, 0),
    "local_credit": func.nullif(JournalVoucherLine.local_credit, 0),
}
# One auxiliary column: `aux:<dim_code>`, sorted by the code it starts with.
# The dim code reaches SQL as a bound parameter, never as text.
_AUX_SORT = re.compile(r"aux:([a-z0-9_]{1,40})")


def _sortable(sort: str | None) -> dict:
    m = _AUX_SORT.fullmatch(sort or "")
    if not m:
        return _SORTABLE
    value = (select(JvLineDimension.value_text)
             .where(JvLineDimension.jv_line_id == JournalVoucherLine.id,
                    JvLineDimension.dim_code == m.group(1))
             .limit(1).scalar_subquery())
    return {**_SORTABLE, sort: lower(value)}


def line_query(f: LineFilters):
    """NC vouchers only, like the voucher list (NC is the book of record; the
    internal drafts never reach it). Line-level filters apply to the line
    itself here, not to "any line of the voucher" as on the voucher list."""
    stmt = (select(JournalVoucherLine, JournalVoucher)
            .join(JournalVoucher, JournalVoucher.id == JournalVoucherLine.jv_id)
            .where(JournalVoucher.nc_source_pk.is_not(None)))
    if f.period_from:
        stmt = stmt.where(JournalVoucher.fiscal_period >= f.period_from)
    if f.period_to:
        stmt = stmt.where(JournalVoucher.fiscal_period <= f.period_to)
    if f.date_from:
        stmt = stmt.where(JournalVoucher.voucher_date >= f.date_from)
    if f.date_to:
        stmt = stmt.where(JournalVoucher.voucher_date <= f.date_to)
    if f.status:
        stmt = stmt.where(JournalVoucher.status == f.status)
    if f.source_subsystem:
        stmt = stmt.where(JournalVoucher.source_subsystem == f.source_subsystem)
    if f.account_code:
        stmt = stmt.where(JournalVoucherLine.account_code.startswith(f.account_code))
    if f.currency:
        stmt = stmt.where(JournalVoucherLine.currency == f.currency)
    if f.aux_code:
        dim = select(JvLineDimension.id).where(
            JvLineDimension.jv_line_id == JournalVoucherLine.id,
            JvLineDimension.dim_code == f.aux_code)
        if f.aux_value:
            dim = dim.where(JvLineDimension.value_text == f.aux_value)
        stmt = stmt.where(dim.exists())
    if f.q:
        like = f"%{f.q}%"
        stmt = stmt.where(or_(JournalVoucher.jv_number.ilike(like),
                              JournalVoucherLine.summary.ilike(like)))
    clauses = order_by(f.sort, f.dir, _sortable(f.sort),          # type: ignore[arg-type]
                       tiebreak=(JournalVoucher.jv_number, JournalVoucherLine.line_no,
                                 JournalVoucherLine.id))
    if clauses:
        return stmt.order_by(*clauses)
    return stmt.order_by(JournalVoucher.voucher_date.desc(), JournalVoucher.jv_number.desc(),
                         JournalVoucherLine.line_no, JournalVoucherLine.id)


async def account_names(db: AsyncSession, codes: set[str]) -> dict[str, str]:
    if not codes:
        return {}
    return dict((await db.execute(select(ChartOfAccount.code, ChartOfAccount.name)
                                  .where(ChartOfAccount.code.in_(codes)))).all())


def _s(v) -> str | None:
    return None if v is None else str(v)


def line_row(ln: JournalVoucherLine, jv: JournalVoucher, acct: dict[str, str],
             dims: list[tuple[str, str | None]], names: AuxNames) -> dict:
    return {
        "jv_id": str(jv.id), "jv_number": jv.jv_number, "voucher_word": jv.voucher_word,
        "voucher_date": jv.voucher_date.isoformat(), "fiscal_period": jv.fiscal_period,
        "nc_num": jv.nc_num, "nc_voucher_type_name": jv.nc_voucher_type_name,
        "status": jv.status, "source_subsystem": jv.source_subsystem,
        "nc_prepared_name": jv.nc_prepared_name,
        "line_no": ln.line_no, "account_code": ln.account_code,
        "account_name": acct.get(ln.account_code) if ln.account_code else None,
        "summary": ln.summary, "currency": ln.currency, "fx_rate": str(ln.fx_rate),
        "orig_debit": str(ln.orig_debit), "orig_credit": str(ln.orig_credit),
        "local_debit": str(ln.local_debit), "local_credit": str(ln.local_credit),
        "quantity": _s(ln.quantity), "unit": ln.unit, "price": _s(ln.price),
        "opposite_subject": ln.opposite_subject,
        "aux": {code: names.display(code, val, ln.partner_name) for code, val in dims},
    }


async def page(db: AsyncSession, f: LineFilters, limit: int, offset: int) -> list[dict]:
    rows = (await db.execute(line_query(f).offset(offset).limit(limit))).all()
    lines = [ln for ln, _ in rows]
    dims = await dims_for(db, [ln.id for ln in lines])
    acct = await account_names(db, {ln.account_code for ln in lines if ln.account_code})
    names = await AuxNames.load(db)
    return [line_row(ln, jv, acct, dims.get(ln.id, []), names) for ln, jv in rows]


# Fixed columns of the CSV, (header, row key). The auxiliaries follow, one
# column each, in book_dim_codes() order.
CSV_COLUMNS = (
    ("Voucher Date", "voucher_date"), ("Period", "fiscal_period"),
    ("Voucher No.", "jv_number"), ("NC No.", "nc_num"),
    ("Voucher Type", "nc_voucher_type_name"), ("Status", "status"),
    ("System", "source_subsystem"), ("Prepared By", "nc_prepared_name"),
    ("Line", "line_no"), ("Account Code", "account_code"), ("Account Name", "account_name"),
    ("Summary", "summary"), ("Currency", "currency"), ("FX Rate", "fx_rate"),
    ("Debit (Orig)", "orig_debit"), ("Credit (Orig)", "orig_credit"),
    ("Debit (CAD)", "local_debit"), ("Credit (CAD)", "local_credit"),
    ("Quantity", "quantity"), ("Unit", "unit"), ("Price", "price"),
    ("Contra Account", "opposite_subject"),
)
_NUMERIC_KEYS = {"nc_num", "line_no", "fx_rate", "orig_debit", "orig_credit",
                 "local_debit", "local_credit", "quantity", "price"}


def _cell(key: str, v) -> str:
    if v is None:
        return ""
    s = str(v)
    # A text cell starting with = + - @ is a formula to Excel ("- adjustment"
    # becomes #NAME?, and a crafted summary could run one). Numbers stay numbers.
    if key not in _NUMERIC_KEYS and s[:1] in ("=", "+", "-", "@"):
        return "'" + s
    return s


async def write_csv(db: AsyncSession, f: LineFilters, out: io.TextIOBase,
                    chunk: int = 5000) -> int:
    """Every matching line, auxiliaries expanded one column each. Paged by line
    id in the list's order so memory stays flat on a whole-book export."""
    codes = await book_dim_codes(db)
    names = await AuxNames.load(db)
    w = csv.writer(out)
    w.writerow([h for h, _ in CSV_COLUMNS] + [dim_label(c) for c in codes])
    ids = (await db.execute(
        line_query(f).with_only_columns(JournalVoucherLine.id))).scalars().all()
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        order = {lid: n for n, lid in enumerate(part)}
        rows = (await db.execute(
            select(JournalVoucherLine, JournalVoucher)
            .join(JournalVoucher, JournalVoucher.id == JournalVoucherLine.jv_id)
            .where(JournalVoucherLine.id.in_(part)))).all()
        rows.sort(key=lambda r: order[r[0].id])
        dims = await dims_for(db, part)
        acct = await account_names(db, {ln.account_code for ln, _ in rows if ln.account_code})
        for ln, jv in rows:
            r = line_row(ln, jv, acct, dims.get(ln.id, []), names)
            w.writerow([_cell(k, r[k]) for _, k in CSV_COLUMNS]
                       + [_cell("aux", r["aux"].get(c)) for c in codes])
    return len(ids)
