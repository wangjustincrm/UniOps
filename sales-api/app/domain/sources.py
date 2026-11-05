"""Source orders: what a Shopify order or a retailer 850 says, in one shape
(PRD A.8, A.11; FR-OB-10, -11b, -13). Pure mapping — matching items and
converting units happens in the order service with the Item Master.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass
class SourceLine:
    line_no: int
    quantity: Decimal                 # current quantity to ship, in the ordered unit
    unit: str | None                  # 850: UOM; Shopify: None (from the variant title)
    identifiers: dict[str, Any]       # gtin, upc, sku, buyer_part, vendor_part, title, variant_title
    unit_price: Decimal | None = None
    inner_packs: Decimal | None = None  # 850 ofInnerPacks (FR-OB-02 case-pack check)
    excluded: str | None = None         # FR-OB-11b: gift card, no shipping, fully refunded


@dataclass
class SourceOrder:
    source_type: str
    partner_code: str
    source_order_id: str
    source_order_number: str | None
    header: dict[str, Any]            # order columns (dates, addresses, totals, Shopify flags…)
    lines: list[SourceLine] = field(default_factory=list)
    updated_at: datetime | None = None  # Shopify updated_at (the cursor)


class SourceError(ValueError):
    """The source cannot be read as an order (a page with a missing id, …)."""


def _dec(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation as e:
        raise SourceError(f"not a number: {v!r}") from e


def _ts(v: Any) -> datetime | None:
    return datetime.fromisoformat(v) if isinstance(v, str) and v else None


def _day(v: Any) -> date | None:
    """Dates as the source wrote them; a timestamp keeps its own local date."""
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
    ts = _ts(s)
    if ts is None:
        raise SourceError(f"not a date: {v!r}")
    return ts.date()


# ── Shopify (PRD A.11) ───────────────────────────────────────────────────────
def _address(a: dict[str, Any] | None) -> dict[str, Any] | None:
    if not a:
        return None
    return {k: a.get(k) for k in ("name", "company", "address1", "address2", "city", "province_code", "zip",
                                  "country_code", "phone")} | {
        "province": a.get("province_code") or a.get("province"), "country": a.get("country_code") or a.get("country")}


def from_shopify(o: dict[str, Any], *, partner_code: str, partner_abbrev: str | None,
                 cutoff: int | None) -> SourceOrder:
    if not o.get("id"):
        raise SourceError("a Shopify order without an id")  # A.11: fails the page
    number = str(o.get("name") or "").lstrip("#") or str(o.get("order_number") or "")
    ship_to = _address(o.get("shipping_address"))
    bill_to = _address(o.get("billing_address"))
    if not bill_to or (bill_to.get("country") or "").upper() not in ("CA", "CANADA"):
        bill_to = ship_to  # A.11: replaced by ship-to when missing or outside Canada
    customer = o.get("customer") or {}
    shipping = (o.get("shipping_lines") or [{}])[0] if o.get("shipping_lines") else {}
    method = shipping.get("code") or shipping.get("title")
    notes = "\n".join(x for x in (o.get("note"), o.get("email"), o.get("phone"), method,
                                  o.get("cancel_reason")) if x)
    shipping_amount = ((o.get("total_shipping_price_set") or {}).get("shop_money") or {}).get("amount")
    try:
        numeric = int(re.sub(r"\D", "", number)) if number else None
    except ValueError:
        numeric = None
    header = {
        "order_date": _day(o.get("created_at")),
        "ship_to": ship_to, "bill_to": bill_to,
        # As Zoho's 940 has it (AC-OB-25, its 1,087 Shopify 940s): the order's phone, else the
        # ship-to's; the Shopify customer id is the N1 id of both parties.
        "contact": {"email": o.get("email") or customer.get("email"),
                    "phone": o.get("phone") or (ship_to or {}).get("phone") or customer.get("phone"),
                    "customer_id": str(customer["id"]) if customer.get("id") else None},
        "notes": notes or None,
        "shipping_method": method,
        "currency": o.get("currency") or "CAD",
        "subtotal": _dec(o.get("subtotal_price")), "tax": _dec(o.get("total_tax")),
        "discount": _dec(o.get("total_discounts")), "shipping": _dec(shipping_amount),
        "total": _dec(o.get("current_total_price") or o.get("total_price")),
        "shopify_financial_status": o.get("financial_status"),
        "shopify_fulfillment_status": o.get("fulfillment_status"),
        "source_cancelled_at": _ts(o.get("cancelled_at")),
        "archived_in_shopify_at": _ts(o.get("closed_at")),
        "source_reference": f"{partner_abbrev}-{number}" if partner_abbrev and number else None,
        "historical": bool(cutoff is not None and numeric is not None and numeric < cutoff),  # FR-OB-17
    }
    refunded: dict[int, Decimal] = {}
    for refund in o.get("refunds") or []:
        for rl in refund.get("refund_line_items") or []:
            refunded[rl["line_item_id"]] = refunded.get(rl["line_item_id"], Decimal(0)) + Decimal(str(rl["quantity"]))
    lines = []
    for li in o.get("line_items") or []:
        ordered = Decimal(str(li.get("quantity") or 0))
        current = li.get("current_quantity")
        qty = Decimal(str(current)) if current is not None else ordered - refunded.get(li["id"], Decimal(0))
        excluded = ("gift card" if li.get("gift_card") else
                    "requires no shipping" if li.get("requires_shipping") is False else
                    "fully refunded or removed" if qty <= 0 else None)  # FR-OB-11b
        lines.append(SourceLine(
            line_no=int(li["id"]), quantity=max(qty, Decimal(0)), unit=None, unit_price=_dec(li.get("price")),
            identifiers={"sku": li.get("sku"), "title": li.get("title") or li.get("name"),
                         "variant_title": li.get("variant_title"), "variant_id": li.get("variant_id")},
            excluded=excluded,
        ))
    return SourceOrder("SHOPIFY", partner_code, str(o["id"]), number or None, header, lines, _ts(o.get("updated_at")))


# ── retailer 850 JSON (PRD A.8) ──────────────────────────────────────────────
# Every key the parser reads, in one place.
#
# VERIFIED AGAINST REAL FILES (2026-11-03: TrueCommerce's 2 real Loblaws
# 850s, AC-9898, on dev2; anonymised shape tests/fixtures/truecommerce_850_real_shape.json):
# - the lines are under "Item" (not "Lines" as PRD A.8 had it): Line "00010",
#   BuyerPart (zero-padded), UPC, GTIN#, Description, Quantity "200" (text),
#   UOM "Case", UnitPrice, ofInnerPacks;
# - the ship-to and bill-to are nested objects: "ShipTo" {ShipToCode (the
#   GLN), ShipToName, ShipToAddress-LineOne / -LineTwo, ShipToCity,
#   ShipToState, ShipToZipCode, ShipToCountry}, "BillTo" {BillToCode,
#   BillToName, BillTo… address}; also "Buying Party", unused;
# - dates: PODate "2026-11-03T00:00:00", RequestedDeliveryDate "11/10/2026";
#   empty values are "" (or null).
# The flat keys of A.8 (ShipToAddress1, …, "Lines") are still read when the
# nested ones are absent (older fixtures; the A.8 sample).
K = {
    "orders": "PurchaseOrder", "partner": "TradingPartner", "po": "PurchaseOrderNumber", "po_date": "PODate",
    "requested": "RequestedDeliveryDate", "dnda": "DoNotDeliverAfter", "cancel": "CancelDate",
    "order_type": "OrderType", "department": "DepartmentNumber", "ship_to_code": "ShipToCode",
    "ship_to_name": "ShipToName", "ship_to_address1": "ShipToAddress1", "ship_to_address2": "ShipToAddress2",
    "ship_to_city": "ShipToCity", "ship_to_state": "ShipToState", "ship_to_zip": "ShipToZip",
    "ship_to_country": "ShipToCountry", "bill_to_name": "BillToName", "bill_to_code": "BillToCode",
    "bill_to_address1": "BillToAddress1", "bill_to_address2": "BillToAddress2", "bill_to_city": "BillToCity",
    "bill_to_state": "BillToState", "bill_to_zip": "BillToZip", "bill_to_country": "BillToCountry",
    "contact_name": "BuyerName", "contact_phone": "BuyerPhone", "contact_email": "BuyerEmail",
    "notes": ("Note1", "Note2", "Note3", "Note4"),
    "lines": "Item", "lines_a8": "Lines", "line": "Line", "vendor_part": "VendorPart", "buyer_part": "BuyerPart", "upc": "UPC",
    "gtin": "GTIN#", "quantity": "Quantity", "uom": "UOM", "unit_price": "UnitPrice", "inner_packs": "ofInnerPacks",
    "description": "Description",
}
# The real files' nested parties (see above) → the flat names K reads.
SHIP_TO_NESTED = {"ShipToCode": "ShipToCode", "ShipToName": "ShipToName", "ShipToAddress-LineOne": "ShipToAddress1",
                  "ShipToAddress-LineTwo": "ShipToAddress2", "ShipToCity": "ShipToCity", "ShipToState": "ShipToState",
                  "ShipToZipCode": "ShipToZip", "ShipToCountry": "ShipToCountry"}
BILL_TO_NESTED = {"BillToCode": "BillToCode", "BillToName": "BillToName", "BillToAddress-LineOne": "BillToAddress1",
                  "BillToAddress-LineTwo": "BillToAddress2", "BillToCity": "BillToCity", "BillToState": "BillToState",
                  "BillToZipCode": "BillToZip", "BillToCountry": "BillToCountry"}
# The 850's unit of measure → the Item Master's unit names ("Case" in the real files).
UOM = {"CA": "CASE", "CS": "CASE", "CASE": "CASE", "EA": "EA", "EACH": "EA"}


def from_850(body: dict[str, Any]) -> list[SourceOrder]:
    """Every PO in the file (FR-OB-13)."""
    pos = body.get(K["orders"])
    if not isinstance(pos, list) or not pos:
        raise SourceError(f"no {K['orders']} list")
    return [_po(po) for po in pos]


def _flatten(po: dict[str, Any]) -> dict[str, Any]:
    """The real files' nested ShipTo / BillTo read as A.8's flat keys (a flat
    key already there wins)."""
    out = dict(po)
    for obj, names in (("ShipTo", SHIP_TO_NESTED), ("BillTo", BILL_TO_NESTED)):
        nested = po.get(obj)
        if isinstance(nested, dict):
            for src, flat in names.items():
                if out.get(flat) in (None, "") and nested.get(src) not in (None, ""):
                    out[flat] = nested[src]
    return out


def _po(po: dict[str, Any]) -> SourceOrder:
    po = _flatten(po)
    partner = str(po.get(K["partner"]) or "").strip()
    number = str(po.get(K["po"]) or "").strip()
    if not partner or not number:
        raise SourceError(f"a PO without {K['partner']} or {K['po']}")
    requested = _day(po.get(K["requested"])) or _day(po.get(K["dnda"]))  # A.8: falls back to DoNotDeliverAfter
    # Walmart's 850s give the bill-to's code and name only; Zoho's 940 and 810 then carry the ship-to's address.
    bill_address = "BillTo" if po.get(K["bill_to_city"]) else "ShipTo"
    bill = {f: po.get(K[f"{'bill_to' if bill_address == 'BillTo' else 'ship_to'}_{f}"]) for f in
            ("address1", "address2", "city", "state", "zip", "country")}
    header = {
        "order_date": _day(po.get(K["po_date"])), "requested_delivery_date": requested,
        "do_not_deliver_after": _day(po.get(K["dnda"])), "cancel_date": _day(po.get(K["cancel"])),
        "order_type": po.get(K["order_type"]), "department": po.get(K["department"]),
        "ship_to": {"gln": po.get(K["ship_to_code"]), "name": po.get(K["ship_to_name"]),
                    "address1": po.get(K["ship_to_address1"]), "address2": po.get(K["ship_to_address2"]),
                    "city": po.get(K["ship_to_city"]), "province": po.get(K["ship_to_state"]),
                    "zip": po.get(K["ship_to_zip"]), "country": po.get(K["ship_to_country"]) or "CA"},
        "bill_to": {"gln": po.get(K["bill_to_code"]), "code": po.get(K["bill_to_code"]), "name": po.get(K["bill_to_name"]),
                    "address1": bill["address1"], "address2": bill["address2"], "city": bill["city"],
                    "province": bill["state"], "zip": bill["zip"], "country": bill["country"] or "CA"},
        "contact": {"name": po.get(K["contact_name"]), "phone": po.get(K["contact_phone"]),
                    "email": po.get(K["contact_email"])},
        "notes": "\n".join(str(po[n]) for n in K["notes"] if po.get(n)) or None,
        "source_reference": number,
    }
    lines = []
    for i, ln in enumerate(po.get(K["lines"]) or po.get(K["lines_a8"]) or [], start=1):
        uom = str(ln.get(K["uom"]) or "").strip().upper()
        lines.append(SourceLine(
            line_no=int(ln.get(K["line"]) or i), quantity=_dec(ln.get(K["quantity"])) or Decimal(0),
            unit=UOM.get(uom, uom or None), unit_price=_dec(ln.get(K["unit_price"])),
            inner_packs=_dec(ln.get(K["inner_packs"])),
            identifiers={"gtin": ln.get(K["gtin"]), "upc": ln.get(K["upc"]), "buyer_part": ln.get(K["buyer_part"]),
                         "vendor_part": ln.get(K["vendor_part"]), "uom": uom or None,
                         "description": ln.get(K["description"]) or None},
        ))
    return SourceOrder("EDI_850", partner, number, number, header, lines)
