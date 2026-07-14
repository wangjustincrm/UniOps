"""NC AP export assembly — parallel-run: UniOps AP invoices -> NC payable-module
import rows (spec 2026-07-14-finance-nc-ap-export-design.md).

billno = UniOps AP number (the JV cross-check anchor). Dimension chains:
  EPMS: invoice_po_allocations (fallback invoices.po_id) -> purchase_requests
        head dims (CC / budget_code / department_name, 100% CC coverage)
  OA:   expense_invoices.pa_id -> payment_applications head dims

2026-07-14 NC trial feedback: all enumeration values are NC CODES (not names);
  department/revexp/cost_center output codes; account classified by CC prefix or
  department keyword (classify_expense_account); buysell is per-row (CAD→2, else→4).
"""
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine
from app.models.coa import ChartOfAccount  # noqa: F401 — kept for COA queries elsewhere
from app.models.mirrors import (BudgetAccount, CostCenter, Department, ExpenseInvoice,
                                Invoice, InvoicePoAllocation, PurchaseRequest, User)
from app.models.pa import PaymentApplication
from app.models.posting import PostingEvent, PostingLine

_ZERO = Decimal("0")

# Fixed enumeration defaults — all values are NC CODES (2026-07-14 trial feedback).
NC_EXPORT_DEFAULTS = {
    "org": "01010104",
    "ap_type": "Payable of Expense",
    "busi_process": "AP01",
    "ap_type_code": "F1-Cxx-017",
    "taxtype": "02",
    "tax_code": "001",
    "tax_rate": "13.00",
    "pay_term": "FH01",
    "tax_country": "Canada",
    "obj_type": "1",
}
TAX_CODE_MAP = {"HST_ON": "001"}

# buysell: CAD → '2', any other currency → '4'
_BUYSELL_CAD = "2"
_BUYSELL_OTHER = "4"

# NC cost-centre code reverse map: UniOps cc.code → NC code
NC_CC_BY_UNIOPS = {
    "MOH-0106-E01": "ENG", "MOH-0104-P01": "P01", "MOH-0104-P02": "P02",
    "MOH-0104-P03": "P03", "MOH-0105-LAB": "Q01", "GA-0105": "QA",
    "MOH-0107-S02": "S02", "SELL-0107-S03": "S03", "GA-0107": "SC",
    "MOH-0101": "H01", "GA-0101": "HR",
}


def _s(v) -> str:
    return str(Decimal(v).quantize(Decimal("0.01")))


def classify_expense_account(cc_code: str | None, department_name: str | None) -> str:
    """Return the NC expense account code.

    When a CC code is present use its UniOps prefix:
        MOH*  → 510101   RD*  → 5301
        SELL* → 660101   GA*  → 6602

    When there is no CC fall back to department-name keyword matching
    (case-insensitive substring):
        Engineering | Production → 510101
        Marketing | Sales | BD | E-COM → 660101
        R&D → 5301
        anything else → 6602
    """
    if cc_code:
        upper = cc_code.upper()
        if upper.startswith("MOH"):
            return "510101"
        if upper.startswith("RD"):
            return "5301"
        if upper.startswith("SELL"):
            return "660101"
        if upper.startswith("GA"):
            return "6602"
    # fallback: department keyword
    dept = (department_name or "").lower()
    if any(kw in dept for kw in ("engineering", "production")):
        return "510101"
    if any(kw in dept for kw in ("marketing", "sales", "bd", "e-com")):
        return "660101"
    if "r&d" in dept:
        return "5301"
    return "6602"


async def _has_accrual(db: AsyncSession, ap_id: uuid.UUID) -> bool:
    """Return True when the AP has an accrual posting (purchase_expense line)."""
    row = (await db.execute(
        select(PostingLine.account_code)
        .join(PostingEvent, PostingLine.event_id == PostingEvent.id)
        .where(PostingEvent.source_doc_type == "ap_invoice",
               PostingEvent.source_doc_id == ap_id,
               PostingEvent.event_type == "accrual",
               PostingLine.line_role == "purchase_expense")
        .limit(1))).scalar_one_or_none()
    return row is not None


async def _lookup_map(db, model, ids, attr="id"):
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    col = getattr(model, attr)
    return {getattr(r, attr): r for r in (await db.execute(
        select(model).where(col.in_(ids)))).scalars()}


async def build_export_rows(db: AsyncSession, ap_ids: list) -> tuple[list, list, list]:
    """-> (heads, bodies, errors). Exportable = status not draft/void AND has an
    accrual posting; failures land in errors, the rest still export."""
    aps = (await db.execute(select(ApInvoice).where(ApInvoice.id.in_(ap_ids))
                            .order_by(ApInvoice.ap_invoice_number))).scalars().all()
    ccs = {c.id: c for c in (await db.execute(select(CostCenter))).scalars()}
    # departments by id — for CC → department_id → code resolution
    depts_by_id = {d.id: d for d in (await db.execute(select(Department))).scalars()}
    # departments by name (lower) — for fallback name→code matching
    depts_by_name: dict[str, Department] = {}
    for dep in depts_by_id.values():
        depts_by_name[dep.name.lower()] = dep
    d = NC_EXPORT_DEFAULTS

    heads, bodies, errors = [], [], []
    seq = 0
    for ap in aps:
        if ap.status in ("draft", "void"):
            errors.append({"ap_id": str(ap.id), "ap_number": ap.ap_invoice_number,
                           "reason": f"status {ap.status}"})
            continue
        if not await _has_accrual(db, ap.id):
            errors.append({"ap_id": str(ap.id), "ap_number": ap.ap_invoice_number,
                           "reason": "no accrual posting"})
            continue

        tax_lines = (await db.execute(select(ApInvoiceTaxLine).where(
            ApInvoiceTaxLine.invoice_id == ap.id))).scalars().all()
        nc_tax_code = TAX_CODE_MAP.get(tax_lines[0].tax_code, d["tax_code"]) \
            if tax_lines and tax_lines[0].tax_code else d["tax_code"]

        buysell = _BUYSELL_CAD if ap.currency == "CAD" else _BUYSELL_OTHER

        def _dept_code_from_cc(cc: CostCenter | None, dept_name: str | None) -> str:
            """Resolve NC department code: via CC.department_id first, then name match."""
            if cc is not None and cc.department_id:
                dept = depts_by_id.get(cc.department_id)
                if dept:
                    return dept.code
            # fallback: case-insensitive substring match on department name
            if dept_name:
                needle = dept_name.lower()
                for dname, dept in depts_by_name.items():
                    if needle in dname or dname in needle:
                        return dept.code
            return ""

        # per-source dimension rows:
        # [(notax, tax, cc_code_uniops, dept_code_nc, revexp_code, employee)]
        rows: list[tuple] = []
        if ap.source == "epms":
            allocs = (await db.execute(select(InvoicePoAllocation).where(
                InvoicePoAllocation.invoice_id == ap.source_invoice_id))).scalars().all()
            po_ids = [a.po_id for a in allocs]
            if not allocs:
                inv = (await db.execute(select(Invoice).where(
                    Invoice.id == ap.source_invoice_id))).scalar_one_or_none()
                po_ids = [inv.po_id] if inv and inv.po_id else [None]
            prs = {}
            if any(po_ids):
                for pr in (await db.execute(select(PurchaseRequest).where(
                        PurchaseRequest.po_id.in_([p for p in po_ids if p])))).scalars():
                    prs[pr.po_id] = pr

            def _dims(po_id):
                pr = prs.get(po_id)
                if pr is None:
                    return ("", "", "", "")
                cc = ccs.get(pr.cost_center_id)
                # NC cost_center code via reverse map
                cc_nc = NC_CC_BY_UNIOPS.get(cc.code, "") if cc else ""
                # NC department code
                dept_code = _dept_code_from_cc(cc, pr.department_name)
                # revexp = budget CODE directly (already a CRM code)
                revexp_code = pr.budget_code or ""
                return (cc_nc, dept_code, revexp_code, "")

            if allocs:
                for a in allocs:
                    cc_nc, dept_code, revexp_code, emp = _dims(a.po_id)
                    # for account classification we need the raw UniOps cc code
                    pr = prs.get(a.po_id)
                    cc_obj = ccs.get(pr.cost_center_id) if pr else None
                    cc_uniops = cc_obj.code if cc_obj else None
                    dept_name = pr.department_name if pr else None
                    rows.append((a.allocated_amount, a.allocated_tax,
                                 cc_nc, dept_code, revexp_code, emp,
                                 cc_uniops, dept_name))
            else:
                cc_nc, dept_code, revexp_code, emp = _dims(po_ids[0])
                pr = prs.get(po_ids[0])
                cc_obj = ccs.get(pr.cost_center_id) if pr else None
                cc_uniops = cc_obj.code if cc_obj else None
                dept_name = pr.department_name if pr else None
                rows.append((ap.amount, ap.tax_amount,
                             cc_nc, dept_code, revexp_code, emp,
                             cc_uniops, dept_name))
        else:  # oa — Direct PA head dims
            cc_nc = dept_code = revexp_code = emp = ""
            cc_uniops = dept_name = None
            ei = (await db.execute(select(ExpenseInvoice).where(
                ExpenseInvoice.id == ap.source_invoice_id))).scalar_one_or_none()
            pa = None
            if ei is not None and ei.pa_id is not None:
                pa = (await db.execute(select(PaymentApplication).where(
                    PaymentApplication.id == ei.pa_id))).scalar_one_or_none()
            if pa is not None:
                cc = ccs.get(pa.cost_center_id)
                cc_nc = NC_CC_BY_UNIOPS.get(cc.code, "") if cc else ""
                cc_uniops = cc.code if cc else None
                dept_code = _dept_code_from_cc(cc, None)
                # revexp = budget CODE directly
                revexp_code = pa.budget_account_code or ""
                creator = await _lookup_map(db, User, [pa.created_by])
                emp = next(iter(creator.values())).full_name if creator else ""
                if cc is not None and cc.department_id:
                    dept = depts_by_id.get(cc.department_id)
                    dept_name = dept.name if dept else None
                else:
                    dept_name = None
            rows.append((ap.amount, ap.tax_amount,
                         cc_nc, dept_code, revexp_code, emp,
                         cc_uniops, dept_name))

        # tax proration when rows carry no tax but the AP does
        total_tax = sum((r[1] for r in rows), _ZERO)
        if total_tax == _ZERO and ap.tax_amount > _ZERO:
            base = sum((r[0] for r in rows), _ZERO)
            prorated, acc = [], _ZERO
            for i, r in enumerate(rows):
                t = (ap.tax_amount - acc if i == len(rows) - 1 else
                     (ap.tax_amount * r[0] / base).quantize(Decimal("0.01"))
                     if base > _ZERO else _ZERO)
                acc += t
                prorated.append((r[0], t, *r[2:]))
            rows = prorated

        first = rows[0]
        # first row tuple: (notax, tax, cc_nc, dept_code, revexp_code, emp, cc_uniops, dept_name)
        heads.append({
            "seq": seq, "billno": ap.ap_invoice_number,
            "ap_type": d["ap_type"], "busi_process": d["busi_process"],
            "billdate": ap.invoice_date.isoformat(), "busidate": ap.invoice_date.isoformat(),
            "obj_type": d["obj_type"], "supplier": ap.vendor_name or "",
            "department": first[3], "employee": first[5], "revexp": first[4],
            "currency": ap.currency, "ap_type_code": d["ap_type_code"],
            "tax_country": d["tax_country"],
        })
        for notax, tax, cc_nc, dept_code, revexp_code, emp, cc_uniops, dept_name in rows:
            notax_d, tax_d = Decimal(notax), Decimal(tax)
            rate = (_s(tax_d / notax_d * 100) if notax_d > _ZERO and tax_d > _ZERO
                    else d["tax_rate"])
            acct_code = classify_expense_account(cc_uniops, dept_name)
            bodies.append({
                "seq": seq, "account_path": acct_code,
                "invoice_no": ap.vendor_invoice_number or ap.ap_invoice_number,
                "summary": f"{ap.vendor_name or ''} {ap.po_number or ''}".strip(),
                "pay_term": d["pay_term"], "obj_type": d["obj_type"],
                "supplier": ap.vendor_name or "", "department": dept_code,
                "cost_center": cc_nc, "employee": emp, "revexp": revexp_code,
                "currency": ap.currency, "rate": "1",
                "money": _s(notax_d + tax_d), "qty": "",
                "tax_code": nc_tax_code, "tax_rate": rate,
                "tax_price": "0.00000000", "notax": _s(notax_d), "tax": _s(tax_d),
                "taxtype": d["taxtype"], "department2": dept_code,
                "buysell": buysell,
            })
        seq += 1
    return heads, bodies, errors


# ── xlsx writer ────────────────────────────────────────────────────────────────────
import io
import os

_ASSET = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "assets", "nc_ap_template.xlsx")

_HEAD_COLS = ["org", "billno", "ap_type", "busi_process", "billdate", "busidate",
              "obj_type", "supplier", "department", "employee", "revexp",
              "currency", "ap_type_code", "tax_country", "", ""]
_BODY_COLS = ["account_path", "invoice_no", "summary", "", "pay_term", "obj_type",
              "supplier", "department", "cost_center", "employee", "", "revexp",
              "currency", "rate", "money", "qty", "tax_code", "tax_rate",
              "tax_price", "notax", "tax", "taxtype", "department2", "", "", "",
              "buysell", "", ""]


def write_xlsx(heads: list, bodies: list) -> bytes:
    """Rebuild the NC import layout on a template copy: notice + head tech row,
    head data rows (A=doc seq), one blank row, body tech row, body data rows.
    Everything written as text (NC requires all-text cells)."""
    import openpyxl
    wb = openpyxl.load_workbook(_ASSET)
    ws = wb["Sheet1"]

    # snapshot the two tech/label rows before clearing sample data
    body_tech = [ws.cell(5, c).value for c in range(1, ws.max_column + 1)]
    ws.delete_rows(3, ws.max_row - 2)   # drop head sample, blank, body tech, body sample

    r = 3
    for h in heads:
        ws.cell(r, 1, str(h["seq"]))
        for i, key in enumerate(_HEAD_COLS, start=2):
            ws.cell(r, i, "" if key == "" else str(h.get(key, "") if key != "org"
                    else NC_EXPORT_DEFAULTS["org"]))
        r += 1
    r += 1                               # blank separator row
    for c, v in enumerate(body_tech, start=1):
        if v is not None:
            ws.cell(r, c, v)
    r += 1
    for b in bodies:
        ws.cell(r, 1, str(b["seq"]))
        for i, key in enumerate(_BODY_COLS, start=2):
            ws.cell(r, i, "" if key == "" else str(b.get(key, "")))
        r += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
