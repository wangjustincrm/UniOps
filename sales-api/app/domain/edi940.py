"""The 940 shipping order to the warehouse (FR-OB-20, Appendix A.2 / A.3),
its validation (FR-OB-29a) and the values it locks (FR-OB-16).

Built from Appendix A and settled by the segment-by-segment replay against
Zoho's 1,215 archived 940s (AC-OB-25, 2026-11-05).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.domain.x12 import TextIssues, ascii_value, interchange, segment

# A.2: the envelope, as Zoho sends it.
SENDER_QUALIFIER, SENDER_ID = "ZZ", "CANADAROYALMILK"
RECEIVER_QUALIFIER, RECEIVER_ID = "12", "9055651212"
ISA_USAGE = "P"  # A.2: P also to the test folder (open question for Radiant: T for test files?)
GS_VERSION = "006030"
# A.3: the two N1 parties that never change.
SHIP_FROM = ("FR", "CANADA ROYAL MILK ULC", "CRM")
WAREHOUSE = ("WH", "RADIANT COLERAINE WAREHOUSE", "CLN02")
ID_QUALIFIER = "94"
LABEL_WALMART, LABEL_OTHER = "UCC_CRM_WMT", "UCC_CRM_LBW"


PUROLATOR, PUROLATOR_EXPRESS, LTL = "Purolator", "Purolator Express", "LTL"
CARRIERS = (PUROLATOR, PUROLATOR_EXPRESS, LTL)

@dataclass(frozen=True)
class Address:
    name: str | None = None
    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    province: str | None = None
    zip: str | None = None
    country: str | None = None
    gln: str | None = None

    @classmethod
    def of(cls, d: dict[str, Any] | None) -> Address:
        d = d or {}
        name = d.get("name") or " ".join(x for x in (d.get("first_name"), d.get("last_name")) if x) or d.get("company")
        return cls(name=name, address1=d.get("address1"), address2=d.get("address2"), city=d.get("city"),
                   province=d.get("province_code") or d.get("province"), zip=d.get("zip"),
                   country=d.get("country_code") or d.get("country"), gln=d.get("gln"))


@dataclass(frozen=True)
class Line940:
    line_no: int
    item_code: str
    qty_ea: int
    buyer_part: str | None
    gtin: str | None
    item_name: str | None
    weight_lb_per_ea: Decimal | None
    warehouse_held: bool


@dataclass(frozen=True)
class Order940:
    """Everything the 940 carries, taken from the order (D: snapshot)."""
    order_id: int
    order_number: int
    retail: bool                 # an 850 order (else Shopify / manual)
    source_order_number: str     # Shopify order number, or the retailer PO
    source_reference: str | None
    partner_code: str
    walmart: bool
    label_code: str | None       # the partner's label code; empty → A.3 rule
    dc_number: str | None
    order_type: str | None
    department: str | None
    requested_delivery: date | None
    ship_to: Address
    bill_to: Address
    contact: dict[str, Any]
    express: bool                # the shipping method says "express"
    lines: tuple[Line940, ...]
    partner_name: str | None = None  # N9*E1: CRM-940_<number>_<name without spaces>_<PO>
    # A manual order goes by the carrier chosen on it (user 2026-11-13): LTL in the retailer's layout,
    # Purolator (Ground / Express) in the parcel layout Shopify's orders use — with the 9-digit internal
    # number in W05-02 (the customer's own text, with spaces, got the first one rejected) and one G62.
    manual: bool = False
    carrier: str | None = None       # "Purolator" / "Purolator Express" / "LTL" (manual orders)


@dataclass
class Built940:
    content: bytes
    control: str
    lines_sent: list[dict[str, Any]]
    locked: dict[str, Any]
    issues: TextIssues
    errors: list[str] = field(default_factory=list)  # validation (FR-OB-29a): not sent when not empty

    @property
    def ok(self) -> bool:
        return not self.errors and not self.issues.errors


def _d(day: date | None) -> str:
    return day.strftime("%Y%m%d") if day else ""


def locked_values(o: Order940) -> dict[str, Any]:
    """What a 940 fixes and FR-OB-16 compares after it is sent: item,
    quantity in EA, warehouse, ship-to (per line, warehouse items only)."""
    return {
        "warehouse": WAREHOUSE[2],
        "ship_to": asdict(o.ship_to),
        "lines": {str(ln.line_no): {"item_code": ln.item_code, "qty_ea": ln.qty_ea}
                  for ln in o.lines if ln.warehouse_held},
    }


def build_940(o: Order940, control_no: int, now: datetime) -> Built940:
    """The 940 as bytes, with its validation. `now` is America/Toronto local
    time (the ISA / GS / G62 "today")."""
    issues = TextIssues()
    v = ascii_value
    ctl = f"{control_no:09d}"
    errors: list[str] = []

    sent = [ln for ln in o.lines if ln.warehouse_held]  # AC-OB-28: never an item the warehouse does not hold
    if not sent:
        errors.append("no line the warehouse holds: nothing to ship")
    for ln in sent:
        if ln.qty_ea <= 0:
            errors.append(f"line {ln.line_no}: quantity {ln.qty_ea} EA")
        if ln.weight_lb_per_ea is None:
            errors.append(f"line {ln.line_no}: item {ln.item_code} has no gross weight per EA")
    st, bt = o.ship_to, o.bill_to
    for label, value in (("ship_to.name", st.name), ("ship_to.address1", st.address1), ("ship_to.city", st.city),
                         ("ship_to.province", st.province), ("ship_to.zip", st.zip)):
        if not (value or "").strip():
            errors.append(f"{label} is empty")
    if o.walmart and not (o.dc_number or "").strip():
        errors.append("a Walmart order needs its DC number (DC routing)")
    if o.manual and o.carrier not in CARRIERS:
        errors.append("a manual order needs its carrier: Purolator, Purolator Express or LTL")

    def party(code: str, a: Address) -> list[str]:
        country = (a.country or "").strip() or "CA"  # AC-OB-30: an empty country is sent as Canada
        if country.upper() in ("CANADA",):
            country = "CA"
        # Retailers: the 850's ship-to / bill-to code. Shopify: the customer id, on both parties.
        party_id = a.gln if o.retail else (o.contact or {}).get("customer_id")
        n1 = segment("N1", code, v(f"{code}.name", a.name, issues), *((ID_QUALIFIER, party_id) if party_id else ()))
        return [n1,
                segment("N3", v(f"{code}.address1", a.address1, issues), v(f"{code}.address2", a.address2, issues)),
                segment("N4", v(f"{code}.city", a.city, issues), v(f"{code}.province", a.province, issues),
                        v(f"{code}.zip", a.zip, issues), v(f"{code}.country", country, issues))]

    body: list[str] = [segment("ST", "940", ctl)]
    if o.retail:
        body.append(segment("W05", "N", f"{o.order_number:09d}", v("po", o.source_order_number, issues)))
    else:
        body.append(segment("W05", "N", v("order_number", o.source_order_number, issues)))
    body.append(segment("N1", SHIP_FROM[0], SHIP_FROM[1], ID_QUALIFIER, SHIP_FROM[2]))
    body.append(segment("N1", WAREHOUSE[0], WAREHOUSE[1], ID_QUALIFIER, WAREHOUSE[2]))
    body += party("ST", st)
    if not o.retail:  # the ship-to's contact (a parcel needs it), between the two parties
        c = o.contact or {}
        body.append(segment("PER", "CN", v("contact.name", c.get("name") or st.name, issues),
                            "EM" if c.get("email") else "", v("contact.email", c.get("email"), issues),
                            "TE" if c.get("phone") else "", v("contact.phone", c.get("phone"), issues)))
    body += party("BT", bt if (bt.name or bt.address1) else st)
    if not o.retail:
        body.append(segment("N9", "8X", "BC"))
        body.append(segment("N9", "E1", v("source_reference", o.source_reference, issues)))
    else:
        label = o.label_code or (LABEL_WALMART if o.walmart else LABEL_OTHER)
        body += [segment("N9", "8X", "BB"),
                 segment("N9", "E1", v("reference", f"CRM-940_{o.order_number:09d}_{''.join((o.partner_name or o.partner_code).split())}"
                                                    f"_{o.source_order_number}", issues)),
                 segment("N9", "LB", label),
                 f"N9*LO*{v('dc_number', o.dc_number, issues)}".rstrip("*"),  # sent even when empty (AC-OB-26), as `N9*LO`
                 ]
        if o.walmart and (o.order_type or "").strip() == "0020":
            body.append(segment("N9", "MR", "0020"))
        body.append(f"N9*DP*{v('department', o.department, issues)}".rstrip("*"))  # sent even when empty (AC-OB-26), as `N9*DP`
    today = now.strftime("%Y%m%d")
    if o.requested_delivery and o.retail:  # the parcel layout carries one date, G62*10
        body.append(segment("G62", "01", _d(o.requested_delivery)))
    body.append(segment("G62", "10", today))
    if o.retail:
        body.append(segment("G62", "38", today))
        if o.requested_delivery:
            body.append(segment("G62", "79", _d(o.requested_delivery)))
        body.append("W66*PP*LT********WIFS")
    else:
        body.append(f"W66*PP*U***{'EXP' if o.express else 'GND'}*****PRLA")
    total_qty, total_weight = 0, Decimal(0)
    lines_sent = []
    for i, ln in enumerate(sent, start=1):
        body.append(segment("LX", i))
        body.append(segment("W01", ln.qty_ea, "EA", "", "VN", v(f"line {ln.line_no}.item_code", ln.item_code, issues),
                            *(("BP", v(f"line {ln.line_no}.buyer_part", ln.buyer_part, issues)) if ln.buyer_part else ())))
        if o.retail:
            if ln.item_name:
                body.append(segment("N9", "E1", v(f"line {ln.line_no}.item_name", ln.item_name, issues)))
            if ln.gtin:
                body.append(segment("N9", "E2", ln.gtin))
            if ln.buyer_part:
                part = v(f"line {ln.line_no}.buyer_part", ln.buyer_part, issues)
                body += [segment("N9", "L9", part), segment("N9", "E3", part)]
        total_qty += ln.qty_ea
        total_weight += Decimal(ln.qty_ea) * (ln.weight_lb_per_ea or Decimal(0))
        lines_sent.append({"line_no": ln.line_no, "lx": i, "item_code": ln.item_code, "qty_ea": ln.qty_ea})
    weight = total_weight.quantize(Decimal("0.01"), ROUND_HALF_UP)  # AC-OB-27: per EA × quantity
    body.append(segment("W76", total_qty, f"{weight:f}", "LB"))
    body.append(segment("SE", len(body) + 1, ctl))

    isa = (f"ISA*00*{'':10}*00*{'':10}*{SENDER_QUALIFIER}*{SENDER_ID:<15}*{RECEIVER_QUALIFIER}*{RECEIVER_ID:<15}*"
           f"{now:%y%m%d}*{now:%H%M}*U*00401*{ctl}*0*{ISA_USAGE}*>")
    gs = segment("GS", "OW", SENDER_ID, RECEIVER_ID, f"{now:%Y%m%d}", f"{now:%H%M}", ctl, "X", GS_VERSION)
    content = interchange([isa, gs, *body, segment("GE", 1, ctl), segment("IEA", 1, ctl)])
    errors += check_structure(content.decode("ascii"), ctl)
    return Built940(content=content, control=ctl, lines_sent=lines_sent, locked=locked_values(o), issues=issues,
                    errors=errors)


def check_structure(text: str, ctl: str) -> list[str]:
    """FR-OB-29a: the mandatory segments, the counts and the control numbers agree."""
    from app.domain.x12 import segments as read
    errs: list[str] = []
    segs = read(text)
    ids = [s[0] for s in segs]
    for need in ("ISA", "GS", "ST", "W05", "N1", "N3", "N4", "G62", "W66", "LX", "W01", "W76", "SE", "GE", "IEA"):
        if need not in ids:
            errs.append(f"segment {need} is missing")
    try:
        isa, gs = segs[0], segs[1]
        st = next(s for s in segs if s[0] == "ST")
        se = next(s for s in segs if s[0] == "SE")
        if not (isa[13] == gs[6] == st[2] == se[2] == ctl):
            errs.append("control numbers differ between ISA13, GS06, ST02 and SE02")
        if int(se[1]) != ids.index("SE") - ids.index("ST") + 1:
            errs.append("SE01 does not count the segments ST…SE")
        if len(isa) != 17 or len(isa[6]) != 15 or len(isa[8]) != 15:
            errs.append("ISA has the wrong layout")
    except (StopIteration, IndexError, ValueError):
        errs.append("the envelope is incomplete")
    return errs
