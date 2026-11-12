"""Invoices (FR-OB-30, 31; Appendix A.9): lines in cases at the 850 price,
and the 810 JSON.

Quantities: the confirmed (delivered) EA quantity, converted back to cases by
the item's CASE factor and truncated (A.9) — but a part case is never
invoiced silently short: it is a problem the caller raises (the invoice holds).
Price: per case at the 850 unit price; a line ordered in another unit is
scaled by the two factors (price × case factor ÷ order-unit factor).

The 810 JSON has the shape of the 102 archived Zoho 810s (AC-OB-44, replayed
2026-11-05): every line of the order, also those not shipped (QuantityShipped
0); UPC carries the GTIN; terms are "NA" with no days, as Zoho has sent since
August; the empty fields are sent empty.
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
    po_date: date | None
    ship_to: dict
    bill_to: dict
    edi_mailbox: str | None = None  # the buyer's TrueCommerce mailbox, from its 850 (Sobeys)


@dataclass(frozen=True)
class Item810:
    item_code: str | None
    buyer_part: str | None
    gtin: str | None
    description: str | None
    shipped_cases: int
    ordered_cases: int
    unit_price: Decimal
    inner_packs: int | None


def _party(prefix: str, country_key: str, zip_key: str, a: dict) -> dict:
    return {f"{prefix}Name": a.get("name") or "", f"{prefix}Address-LineOne": a.get("address1") or "",
            f"{prefix}Address-LineTwo": a.get("address2") or "", f"{prefix}City": a.get("city") or "",
            f"{prefix}State": a.get("province") or "", zip_key: a.get("zip") or "", country_key: a.get("country") or "CA",
            f"{prefix}Code": a.get("gln") or a.get("code") or ""}


def build_810(h: Header810, items: list[Item810]) -> bytes:
    """A.9: one invoice; InvoiceDate = ShipDate = the delivery date."""
    d = h.invoice_date.strftime("%Y-%m-%d")
    body = {"Invoice": [{
        "TransactionType": "810", "AccountingID": h.partner_code,
        # Only when the 850 named one: a partner without (Walmart, Loblaws) gets the file as before.
        **({"EDIMailbox": h.edi_mailbox} if h.edi_mailbox else {}),
        "Invoice": str(h.order_number), "InvoiceDate": d,
        "PO": h.po, "PODate": h.po_date.strftime("%Y-%m-%d") if h.po_date else "", "DepartmentNumber": "",
        "BillofLading": f"{h.order_number:09d}", "CarrierPro": f"CRM|{h.order_number}", "SCAC": "WIFS", "ShipVia": "",
        "ShipTo": _party("ShipTo", "ShiptoCountry", "ShipToZipcode", h.ship_to),
        "BillTo": _party("Billto", "BilltoCountry", "BilltoZipcode", h.bill_to),
        "ShipDate": d, "TermsDescription": "NA", "NetDaysDue": 0, "DiscountDaysDue": 0, "DiscountPercent": 0, "Note": "",
        "Weight": 0, "TotalCasesShipped": float(sum(i.shipped_cases for i in items)), "TaxAmount": "", "ChargeAmount1": "",
        "ChargeAmount2": "", "AllowancePercent1": "", "AllowanceAmount1": "", "AllowancePercent2": "", "AllowanceAmount2": "",
        "Items": [{"Line": n, "VendorPart": i.item_code or i.buyer_part or "", "BuyerPart": i.buyer_part or "", "UPC": i.gtin or "",
                   "Description": i.description or "", "QuantityShipped": i.shipped_cases, "UOM": "CA",
                   "UnitPrice": float(i.unit_price), "QuantityOrdered": i.ordered_cases, "PackSize": 1,
                   "ofInnerPacks": i.inner_packs or "", "ItemAllowancePercent": "", "ItemAllowanceAmount": "",
                   "GTIN#": i.gtin or ""} for n, i in enumerate(items, start=1)],
    }]}
    return json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
