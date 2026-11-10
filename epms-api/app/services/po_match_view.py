"""A PO against its invoices and receipts, worked out by code.

"Is the invoice for PO-535-2609-02 over the PO, and by how much?" was answered
"difference 0.00, fully matched" (ISS-0016): the stored variance covers only
the PO lines an invoice was allocated to, so a line nobody invoiced — the
transport fee — never shows in it. "What is the three-way match result?" was
answered "not matched" for the same PO, because of the 22.60 that line leaves
and because the PO is partially received (ISS-0036) — neither of which the
rule looks at.

Both answers are arithmetic and a rule, so both are done here, from the rows
matching wrote (INV-3WM-001/002/006), and the chat layer says the result under
the reply whatever the narrator made of it.

Read-only. The PO must be visible to the caller and the caller must hold View
Invoices; an invoice reaches anyone who can see its PO (access_scope invoice),
so every invoice counted here is one they could open.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import or_, select

from app.core.access_scope import is_po_visible
from app.core.po_facts import PASSED_MATCHING, three_way_match_passed
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.po import PoLineItem, PurchaseOrder

# A GR in one of these is not evidence of anything (receipt_evidence).
_DEAD_GR = ("cancelled", "rejected")
_STATUS_NAMES = {"unmatched": "Unmatched", "match_review": "Pending Review",
                 "matched": "Matched", "exception": "Exception", "paid": "Paid",
                 "partially_paid": "Partially Paid", "cancelled": "Cancelled"}
_STATUS_ZH = {"unmatched": "未匹配", "match_review": "待审核", "matched": "已匹配",
              "exception": "异常", "paid": "已付款", "partially_paid": "部分付款",
              "cancelled": "已取消"}


def _money(value) -> str:
    return f"{Decimal(value or 0):,.2f}"


def _date(value) -> str | None:
    if value is None:
        return None
    from zoneinfo import ZoneInfo

    from app.core.delegation import PLANT_TIMEZONE
    local = value.astimezone(ZoneInfo(PLANT_TIMEZONE)) if getattr(value, "tzinfo", None) else value
    return local.strftime("%d/%m/%Y")


async def compare(db, scope: dict, po_number: str) -> dict | None:
    """The PO's lines against everything invoiced on them, and its match facts.
    None when the PO is not there or not this caller's to see."""
    perms = scope.get("perms") or {}
    if perms and not perms.get("view_invoice", False):
        return None
    po = (await db.execute(select(PurchaseOrder).where(
        PurchaseOrder.number == po_number))).scalar_one_or_none()
    if po is None or not await is_po_visible(db, po.id, scope):
        return None

    lines = (await db.execute(select(PoLineItem).where(PoLineItem.po_id == po.id)
                              .order_by(PoLineItem.sort_order))).scalars().all()
    allocated_ids = select(InvoicePoAllocation.invoice_id).where(
        InvoicePoAllocation.po_id == po.id)
    invoices = (await db.execute(select(Invoice).where(
        or_(Invoice.po_id == po.id, Invoice.id.in_(allocated_ids)),
        Invoice.status != "cancelled").order_by(Invoice.internal_ref))).scalars().all()
    allocations = (await db.execute(select(InvoicePoAllocation).where(
        InvoicePoAllocation.po_id == po.id,
        InvoicePoAllocation.invoice_id.in_([i.id for i in invoices] or [None])))).scalars().all()
    receipts = (await db.execute(select(GoodsReceipt).where(
        GoodsReceipt.po_id == po.id, GoodsReceipt.status.notin_(_DEAD_GR))
        .order_by(GoodsReceipt.number))).scalars().all()

    on_line: dict = {}
    on_header = Decimal(0)
    allocated_invoices = set()
    for a in allocations:
        allocated_invoices.add(a.invoice_id)
        if a.po_line_id is None:
            on_header += Decimal(a.allocated_amount or 0)
        else:
            on_line[a.po_line_id] = on_line.get(a.po_line_id, Decimal(0)) + Decimal(a.allocated_amount or 0)
    # An invoice matched on the header with no allocation rows bills the PO as
    # a whole (INV-3WM-001: header allocations compare against the subtotal).
    for inv in invoices:
        if inv.id not in allocated_invoices and inv.po_id == po.id:
            on_header += Decimal(inv.amount or 0)

    line_rows = []
    for n, line in enumerate(lines, start=1):
        value = Decimal(line.line_total or 0)
        invoiced = on_line.get(line.id, Decimal(0))
        line_rows.append({"line": n, "description": line.description,
                          "value": str(value), "invoiced": str(invoiced),
                          "difference": str(invoiced - value)})
    po_pre_tax = Decimal(po.subtotal or 0)
    invoiced_pre_tax = sum((Decimal(r["invoiced"]) for r in line_rows), Decimal(0)) + on_header
    # Tax compared on the invoices' own figures, not re-computed.
    invoiced_total = sum((Decimal(i.total_amount or 0) for i in invoices), Decimal(0))

    linked_gr = [r.number for r in receipts]
    # INV-3WM-006, by the one definition of it: matched to the PO (header or
    # allocation) and something received; no quantity is compared and
    # under-invoicing passes (INV-3WM-002).
    three_way = bool((await db.execute(select(three_way_match_passed(po.id)))).scalar())
    return {
        "po_number": po.number, "currency": po.currency,
        "po_pre_tax": str(po_pre_tax), "po_total": str(Decimal(po.total or 0)),
        "invoiced_pre_tax": str(invoiced_pre_tax), "invoiced_total": str(invoiced_total),
        "difference_pre_tax": str(invoiced_pre_tax - po_pre_tax),
        "difference_total": str(invoiced_total - Decimal(po.total or 0)),
        "over_the_po": invoiced_pre_tax > po_pre_tax,
        "lines": line_rows,
        "lines_not_invoiced": [r for r in line_rows if Decimal(r["invoiced"]) == 0
                               and Decimal(r["value"]) != 0],
        "invoices": [{"number": i.internal_ref, "status": i.status,
                      "status_name": _STATUS_NAMES.get(i.status, i.status),
                      "matched_on": _date(i.matched_at),
                      "pre_tax": str(Decimal(i.amount or 0)),
                      "total": str(Decimal(i.total_amount or 0))} for i in invoices],
        "goods_receipts": linked_gr,
        "three_way_matched": three_way,
    }


def sentences(view: dict) -> tuple[str, str]:
    """What must be said about the comparison, in English and Chinese."""
    cur = view["currency"] or ""
    if not view["invoices"]:
        return (f"{view['po_number']} has no invoice yet (cancelled ones not counted).",
                f"{view['po_number']} 还没有发票（已取消的不计）。")
    diff = Decimal(view["difference_pre_tax"])
    diff_total = Decimal(view["difference_total"])
    numbers = ", ".join(i["number"] for i in view["invoices"])
    if diff > 0:
        en_v, zh_v = (f"invoiced {cur} {_money(diff)} MORE than the PO before tax",
                      f"税前比 PO 多开 {cur} {_money(diff)}")
    elif diff < 0:
        en_v, zh_v = (f"{cur} {_money(-diff)} below the PO before tax "
                      f"({cur} {_money(-diff_total)} including tax) — not over the PO",
                      f"税前比 PO 少 {cur} {_money(-diff)}（含税少 {cur} {_money(-diff_total)}），没有超过 PO")
    else:
        en_v, zh_v = ("exactly the PO's pre-tax amount", "与 PO 税前金额完全相同")
    en = [f"Code check of {view['po_number']}: PO before tax {cur} {_money(view['po_pre_tax'])}, "
          f"invoiced before tax {cur} {_money(view['invoiced_pre_tax'])} ({numbers}) — {en_v}."]
    zh = [f"系统核对 {view['po_number']}：PO 税前 {cur} {_money(view['po_pre_tax'])}，"
          f"已开票税前 {cur} {_money(view['invoiced_pre_tax'])}（{numbers}）——{zh_v}。"]
    missing = view["lines_not_invoiced"]
    if missing:
        en.append("Not invoiced: " + "; ".join(
            f"line {r['line']} {r['description']} ({cur} {_money(r['value'])})" for r in missing) + ".")
        zh.append("未开票的行：" + "；".join(
            f"第 {r['line']} 行 {r['description']}（{cur} {_money(r['value'])}）" for r in missing) + "。")
    other = [r for r in view["lines"] if r not in missing and Decimal(r["difference"]) != 0]
    if other:
        en.append("Other line differences: " + "; ".join(
            f"line {r['line']} {cur} {_money(r['difference'])}" for r in other) + ".")
        zh.append("其他行差异：" + "；".join(
            f"第 {r['line']} 行 {cur} {_money(r['difference'])}" for r in other) + "。")
    en.append("Invoice status: " + "; ".join(
        f"{i['number']} {i['status_name']}" + (f", matched {i['matched_on']}" if i["matched_on"] else "")
        for i in view["invoices"]) + ".")
    zh.append("发票状态：" + "；".join(
        f"{i['number']} {_STATUS_ZH.get(i['status'], i['status_name'])}"
        + (f"，{i['matched_on']} 匹配" if i["matched_on"] else "") for i in view["invoices"]) + "。")
    grs = ", ".join(view["goods_receipts"])
    if view["three_way_matched"]:
        en.append(f"Three-way matched (INV-3WM-006: an invoice matched to the PO and a goods "
                  f"receipt linked{' — ' + grs if grs else ''}; quantities are not compared and "
                  f"invoicing less than the PO passes).")
        zh.append(f"三单匹配：已通过（INV-3WM-006：发票已匹配到该 PO 且有收货"
                  f"{'——' + grs if grs else ''}；不比较数量，少开票也算通过）。")
    else:
        why_en = ("no invoice has been matched" if not any(
            i["status"] in PASSED_MATCHING for i in view["invoices"]) else "nothing has been received")
        why_zh = "还没有已匹配的发票" if why_en.startswith("no invoice") else "还没有收货"
        en.append(f"Not three-way matched yet: {why_en} (INV-3WM-006).")
        zh.append(f"三单匹配：尚未通过——{why_zh}（INV-3WM-006）。")
    return " ".join(en), "".join(zh)
