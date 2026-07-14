"""NC AP export assembly — parallel-run: UniOps AP invoices -> NC payable-module
import rows (spec 2026-07-14-finance-nc-ap-export-design.md).

billno = UniOps AP number (the JV cross-check anchor). Dimension chains:
  EPMS: invoice_po_allocations (fallback invoices.po_id) -> purchase_requests
        head dims (CC / budget_code / department_name, 100% CC coverage)
  OA:   expense_invoices.pa_id -> payment_applications head dims
Expense account = the accrual posting's purchase_expense line (fallback account
granularity for now — line-level real accounts are a later project).
"""
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine
from app.models.coa import ChartOfAccount
from app.models.mirrors import (BudgetAccount, CostCenter, Department, ExpenseInvoice,
                                Invoice, InvoicePoAllocation, PurchaseRequest, User)
from app.models.pa import PaymentApplication
from app.models.posting import PostingEvent, PostingLine

_ZERO = Decimal("0")

# Fixed enumeration defaults (user 2026-07-14: all fixed, config-by-constant).
NC_EXPORT_DEFAULTS = {
    "org": "Canada Royal Milk ULC",
    "ap_type": "Payable of Expense",
    "busi_process": "选择付款",
    "ap_type_code": "F1-Cxx-017",
    "buysell": "Domestic Purchases",
    "taxtype": "Tax Excluded",
    "tax_code": "001",
    "tax_rate": "13.00",
    "pay_term": "net 30 days",
    "tax_country": "Canada",
    "obj_type": "Supplier",
}
TAX_CODE_MAP = {"HST_ON": "001"}


def _s(v) -> str:
    return str(Decimal(v).quantize(Decimal("0.01")))


def account_full_path(coa: dict, code: str) -> str:
    """NC subjcode: 'code\\top-name\\...\\leaf-name' walking parent_code upward."""
    acct = coa.get(code)
    if acct is None:
        return code
    names, cur = [], acct
    while cur is not None:
        names.append(cur.name)
        cur = coa.get(cur.parent_code) if cur.parent_code else None
    return "\\".join([code] + list(reversed(names)))


async def _expense_account(db: AsyncSession, ap_id: uuid.UUID) -> str | None:
    """purchase_expense line's account on the AP's accrual posting; None = no accrual."""
    row = (await db.execute(
        select(PostingLine.account_code)
        .join(PostingEvent, PostingLine.event_id == PostingEvent.id)
        .where(PostingEvent.source_doc_type == "ap_invoice",
               PostingEvent.source_doc_id == ap_id,
               PostingEvent.event_type == "accrual",
               PostingLine.line_role == "purchase_expense")
        .limit(1))).scalar_one_or_none()
    return row


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
    coa = {a.code: a for a in (await db.execute(select(ChartOfAccount))).scalars()}
    ccs = {c.id: c for c in (await db.execute(select(CostCenter))).scalars()}
    bas = {b.code: b for b in (await db.execute(select(BudgetAccount))).scalars()}
    d = NC_EXPORT_DEFAULTS

    heads, bodies, errors = [], [], []
    seq = 0
    for ap in aps:
        if ap.status in ("draft", "void"):
            errors.append({"ap_id": str(ap.id), "ap_number": ap.ap_invoice_number,
                           "reason": f"status {ap.status}"})
            continue
        acct_code = await _expense_account(db, ap.id)
        if acct_code is None:
            errors.append({"ap_id": str(ap.id), "ap_number": ap.ap_invoice_number,
                           "reason": "no accrual posting"})
            continue
        acct_path = account_full_path(coa, acct_code)

        tax_lines = (await db.execute(select(ApInvoiceTaxLine).where(
            ApInvoiceTaxLine.invoice_id == ap.id))).scalars().all()
        nc_tax_code = TAX_CODE_MAP.get(tax_lines[0].tax_code, d["tax_code"]) \
            if tax_lines and tax_lines[0].tax_code else d["tax_code"]

        # per-source dimension rows: [(notax, tax, cc_name, dept_name, revexp_name, employee)]
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
                ba = bas.get(pr.budget_code) if pr.budget_code else None
                return (cc.name if cc else "", pr.department_name or "",
                        ba.name if ba else "", "")

            if allocs:
                for a in allocs:
                    cc_n, dept_n, rev_n, emp = _dims(a.po_id)
                    rows.append((a.allocated_amount, a.allocated_tax,
                                 cc_n, dept_n, rev_n, emp))
            else:
                cc_n, dept_n, rev_n, emp = _dims(po_ids[0])
                rows.append((ap.amount, ap.tax_amount, cc_n, dept_n, rev_n, emp))
        else:  # oa — Direct PA head dims
            cc_n = dept_n = rev_n = emp = ""
            ei = (await db.execute(select(ExpenseInvoice).where(
                ExpenseInvoice.id == ap.source_invoice_id))).scalar_one_or_none()
            pa = None
            if ei is not None and ei.pa_id is not None:
                pa = (await db.execute(select(PaymentApplication).where(
                    PaymentApplication.id == ei.pa_id))).scalar_one_or_none()
            if pa is not None:
                cc = ccs.get(pa.cost_center_id)
                cc_n = cc.name if cc else ""
                dept_n = ""
                if cc is not None and cc.department_id:
                    dept = (await db.execute(select(Department).where(
                        Department.id == cc.department_id))).scalar_one_or_none()
                    dept_n = dept.name if dept else ""
                ba = bas.get(pa.budget_account_code) if pa.budget_account_code else None
                rev_n = ba.name if ba else ""
                creator = await _lookup_map(db, User, [pa.created_by])
                emp = next(iter(creator.values())).full_name if creator else ""
            rows.append((ap.amount, ap.tax_amount, cc_n, dept_n, rev_n, emp))

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
        heads.append({
            "seq": seq, "billno": ap.ap_invoice_number,
            "ap_type": d["ap_type"], "busi_process": d["busi_process"],
            "billdate": ap.invoice_date.isoformat(), "busidate": ap.invoice_date.isoformat(),
            "obj_type": d["obj_type"], "supplier": ap.vendor_name or "",
            "department": first[3], "employee": first[5], "revexp": first[4],
            "currency": ap.currency, "ap_type_code": d["ap_type_code"],
            "tax_country": d["tax_country"],
        })
        for notax, tax, cc_n, dept_n, rev_n, emp in rows:
            notax_d, tax_d = Decimal(notax), Decimal(tax)
            rate = (_s(tax_d / notax_d * 100) if notax_d > _ZERO and tax_d > _ZERO
                    else d["tax_rate"])
            bodies.append({
                "seq": seq, "account_path": acct_path,
                "invoice_no": ap.vendor_invoice_number or ap.ap_invoice_number,
                "summary": f"{ap.vendor_name or ''} {ap.po_number or ''}".strip(),
                "pay_term": d["pay_term"], "obj_type": d["obj_type"],
                "supplier": ap.vendor_name or "", "department": dept_n,
                "cost_center": cc_n, "employee": emp, "revexp": rev_n,
                "currency": ap.currency, "rate": "1",
                "money": _s(notax_d + tax_d), "qty": "",
                "tax_code": nc_tax_code, "tax_rate": rate,
                "tax_price": "0.00000000", "notax": _s(notax_d), "tax": _s(tax_d),
                "taxtype": d["taxtype"], "department2": dept_n,
                "buysell": d["buysell"],
            })
        seq += 1
    return heads, bodies, errors
