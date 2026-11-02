"""Invoices (FR-OB-30, 31; Appendix A.9): lines in cases at the 850 price,
and the 810 JSON.

Quantities: the confirmed (delivered) EA quantity, converted back to cases by
the item's CASE factor and truncated (A.9) — but a part case is never
invoiced silently short: it is a problem the caller raises (the invoice holds).
Price: per case at the 850 unit price; a line ordered in another unit is
scaled by the two factors (price × case factor ÷ order-unit factor).

ASSUMPTIONS for the 810 JSON beyond what A.9 names (AccountingID, Invoice,
InvoiceDate, ShipDate, SCAC, CarrierPro, QuantityShipped, empty tax / charges /
allowances) — the line keys mirror the 850's (A.8): Lines / Line / BuyerPart /
VendorPart / UPC / GTIN# / UOM / UnitPrice; PurchaseOrderNumber, Terms,
Currency, Total. AC-OB-44 (replay against archived 810s) settles them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CASE_UNIT = "CASE"
CENT = Decimal("0.01")


@dataclass(frozen=True)
class LineIn:
    order_line_id: int
    line_no: int
    item_code: str | None
    buyer_part: str | None
    upc: str | None
    gtin: str | None
    description: str | None
    delivered_ea: int
    case_factor: int | None          # the item's CASE factor in EA
    order_factor: Decimal | None     # the factor of the unit the line was ordered in
    unit_price: Decimal | None       # per order unit (850)


@dataclass
class LineOut:
    line_no: int
    order_line_id: int
    item_code: str | None
    buyer_part: str | None
    description: str | None
    uom: str
    qty: int
    unit_price: Decimal
    amount: Decimal
    upc: str | None = None
    gtin: str | None = None


@dataclass
class Built:
    lines: list[LineOut] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def subtotal(self) -> Decimal:
        return sum((ln.amount for ln in self.lines), Decimal(0)).quantize(CENT)


def build_lines(lines: list[LineIn]) -> Built:
    out = Built()
    for i, ln in enumerate(sorted(lines, key=lambda x: x.line_no), start=1):
        if ln.delivered_ea <= 0:
            continue
        name = ln.item_code or f"line {ln.line_no}"
        if not ln.case_factor:
            out.problems.append(f"{name}: the item has no CASE unit — cannot invoice per case")
            continue
        if ln.unit_price is None or not ln.order_factor:
            out.problems.append(f"{name}: no unit price from the order")
            continue
        cases, rest = divmod(ln.delivered_ea, ln.case_factor)
        if rest:
            out.problems.append(f"{name}: {ln.delivered_ea} EA is not a whole number of cases of {ln.case_factor} "
                                f"({cases} cases + {rest} EA) — invoiced as {cases} only if a person confirms")
        price = (ln.unit_price * Decimal(ln.case_factor) / ln.order_factor).quantize(Decimal("0.00001"), ROUND_HALF_UP)
        out.lines.append(LineOut(line_no=i, order_line_id=ln.order_line_id, item_code=ln.item_code,
                                 buyer_part=ln.buyer_part, description=ln.description, uom="CA", qty=cases,
                                 unit_price=price, amount=(price * cases).quantize(CENT, ROUND_HALF_UP),
                                 upc=ln.upc, gtin=ln.gtin))
    return out


def amount(qty: int, unit_price: Decimal) -> Decimal:
    return (unit_price * qty).quantize(CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class Header810:
    partner_code: str
    order_number: int
    po: str | None
    invoice_date: date
    terms: str | None
    currency: str


def build_810(h: Header810, lines: list[LineOut]) -> bytes:
    """A.9: one invoice; InvoiceDate = ShipDate = the delivery date."""
    total = sum((ln.amount for ln in lines), Decimal(0)).quantize(CENT)
    d = h.invoice_date.strftime("%Y-%m-%d")
    body = {"Invoice": [{
        "AccountingID": h.partner_code, "Invoice": str(h.order_number), "PurchaseOrderNumber": h.po,
        "InvoiceDate": d, "ShipDate": d, "SCAC": "WIFS", "CarrierPro": f"CRM|{h.order_number}",
        "Terms": h.terms, "Currency": h.currency, "Total": str(total),
        "Taxes": [], "Charges": [], "Allowances": [],
        "Lines": [{"Line": ln.line_no, "BuyerPart": ln.buyer_part, "VendorPart": ln.item_code, "UPC": ln.upc,
                   "GTIN#": ln.gtin, "QuantityShipped": ln.qty, "UOM": ln.uom, "UnitPrice": str(ln.unit_price)}
                  for ln in lines],
    }]}
    return json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
