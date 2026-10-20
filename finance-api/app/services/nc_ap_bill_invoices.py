"""The supplier invoices behind one NC payable.

An NC 应付单 is a header over LINES, and the invoice number lives on the line:
one bill routinely carries several invoices — D12026092800139647 is seven
invoices from one supplier, 4,700.13 in all. On production (2026-10-20) 1,872
of 12,006 approved bills carry more than one invoice number, and 65 of the 364
still open.

The bill-level lists used to show `min(l.invoice_no)` next to the bill's total,
which reads as "this one invoice is 4,700.13". Every list that shows a bill as
one row therefore carries the breakdown as well: each invoice with its own
billed and open amounts, which add up to the row's totals by construction —
they are aggregated over exactly the lines the row was.
"""
from __future__ import annotations

import json
from decimal import Decimal


def lines_agg(alias: str = "l") -> str:
    """A json_agg over the grouped lines, for the SELECT list of a query that
    groups NC bill lines into one row per bill. Money goes out as text so it
    comes back as an exact Decimal, not a float."""
    p = f"{alias}." if alias else ""
    return (f"json_agg(json_build_object("
            f"'invoice_no', {p}invoice_no, 'invoice_no_norm', {p}invoice_no_norm, "
            f"'purchase_order', {p}purchase_order, 'summary', {p}scomment, "
            f"'money_cr', {p}money_cr::text, 'money_bal', {p}money_bal::text) "
            f"order by {p}row_no nulls last, {p}nc_pk)")


def _dec(v) -> Decimal:
    return Decimal(v) if v not in (None, "") else Decimal("0")


def invoices(raw) -> list[dict]:
    """One entry per invoice number, in NC's line order.

    The same number on several lines (one invoice split over budget lines) is
    ONE invoice, so lines are merged on the normalised number; lines with no
    number at all are merged into a single unnumbered entry rather than shown
    as several blanks.
    """
    if raw is None:
        return []
    rows = json.loads(raw) if isinstance(raw, str) else raw
    merged: dict[str | None, dict] = {}
    for r in rows:
        key = r.get("invoice_no_norm") or None
        e = merged.get(key)
        if e is None:
            e = merged[key] = {"invoice_no": (r.get("invoice_no") or "").strip() or None,
                               "purchase_orders": [], "summary": None, "lines": 0,
                               "money_cr": Decimal("0"), "money_bal": Decimal("0")}
        e["lines"] += 1
        e["money_cr"] += _dec(r.get("money_cr"))
        e["money_bal"] += _dec(r.get("money_bal"))
        po = (r.get("purchase_order") or "").strip()
        if po and po not in e["purchase_orders"]:
            e["purchase_orders"].append(po)
        if not e["summary"] and (r.get("summary") or "").strip():
            e["summary"] = r["summary"].strip()
    return [{**e, "money_cr": str(e["money_cr"]), "money_bal": str(e["money_bal"])}
            for e in merged.values()]
