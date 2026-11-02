"""943 warehouse stock transfer shipment advice (Appendix A.4; FR-OB-81).

As Zoho sends it: W06*F with the 5-digit transfer number, N1 SF / ST from the
warehouses' configuration (FR-OB-27), the NTE naming the transfer and file,
W27*M*LT*PP, one W04 + N9*LI per line (EA, two decimals, no lot) and W03 with
the totals. One control number for ISA13 = GS06 = ST02 from the shared
sequence (FR-OB-22); validated before it is queued (FR-OB-29a).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.domain.edi940 import GS_VERSION, ISA_USAGE, RECEIVER_ID, RECEIVER_QUALIFIER, SENDER_ID, SENDER_QUALIFIER
from app.domain.x12 import TextIssues, ascii_value, interchange, segment, segments

TWO = Decimal("0.01")


@dataclass(frozen=True)
class Party943:
    name: str
    id_qualifier: str | None   # 94 in Zoho's files
    id: str | None             # CRM / CLN02
    address1: str | None
    city: str | None
    province: str | None
    zip: str | None
    country: str | None        # omitted when empty (the sample's ST has none)


@dataclass(frozen=True)
class Line943:
    line_no: int               # N9*LI sequence
    item_code: str
    qty_ea: int
    weight_lb_per_ea: Decimal | None


@dataclass(frozen=True)
class Transfer943:
    number: str                # 5 digits, e.g. 00104
    ship_from: Party943
    ship_to: Party943
    lines: tuple[Line943, ...]


@dataclass
class Built943:
    content: bytes
    control: str
    file_name: str
    errors: list[str] = field(default_factory=list)
    issues: TextIssues = field(default_factory=TextIssues)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.issues.errors


def file_name(control: str, number: str) -> str:
    """A.10: CRM-943_<control>_TRID_<transfer no>_.EDI"""
    return f"CRM-943_{control}_TRID_{number}_.EDI"


def _party(qual: str, p: Party943, issues: TextIssues) -> list[str]:
    v = ascii_value
    out = [segment("N1", qual, v(f"{qual}.name", p.name, issues), p.id_qualifier, v(f"{qual}.id", p.id or "", issues) or None)]
    if p.address1:
        out.append(segment("N3", v(f"{qual}.address1", p.address1, issues)))
    out.append(segment("N4", v(f"{qual}.city", p.city or "", issues), p.province, v(f"{qual}.zip", p.zip or "", issues),
                       p.country or None))
    return out


def build_943(t: Transfer943, control_no: int, now: datetime) -> Built943:
    """`now` is America/Toronto local time (the W06 / ISA / GS "today")."""
    issues = TextIssues()
    ctl = f"{control_no:09d}"
    name = file_name(ctl, t.number)
    errors: list[str] = []
    for side, p in (("ship-from", t.ship_from), ("ship-to", t.ship_to)):
        for f in ("name", "id", "city", "province", "zip"):
            if not getattr(p, f):
                errors.append(f"{side} warehouse: {f} is empty")
    if not t.lines:
        errors.append("no line: nothing to transfer")
    body = [segment("ST", "943", ctl), segment("W06", "F", t.number, f"{now:%Y%m%d}", t.number),
            *_party("SF", t.ship_from, issues), *_party("ST", t.ship_to, issues),
            segment("NTE", "OTH", f"Transfer Order - TO-{t.number}_{name}"), segment("W27", "M", "LT", "PP")]
    total_qty, total_weight = Decimal(0), Decimal(0)
    for ln in t.lines:
        if ln.qty_ea <= 0:
            errors.append(f"line {ln.line_no}: quantity {ln.qty_ea} EA")
        if ln.weight_lb_per_ea is None:
            errors.append(f"line {ln.line_no}: {ln.item_code} has no gross weight per EA in the Item Master")
        body.append(segment("W04", f"{Decimal(ln.qty_ea).quantize(TWO)}", "EA", "", "VN",
                            ascii_value(f"line {ln.line_no}.item_code", ln.item_code, issues)))
        body.append(segment("N9", "LI", ln.line_no))
        total_qty += ln.qty_ea
        total_weight += Decimal(ln.qty_ea) * (ln.weight_lb_per_ea or Decimal(0))
    body.append(segment("W03", f"{total_qty.quantize(TWO)}", f"{total_weight.quantize(TWO, ROUND_HALF_UP)}", "LB"))
    body.append(segment("SE", len(body) + 1, ctl))
    isa = (f"ISA*00*{'':10}*00*{'':10}*{SENDER_QUALIFIER}*{SENDER_ID:<15}*{RECEIVER_QUALIFIER}*{RECEIVER_ID:<15}*"
           f"{now:%y%m%d}*{now:%H%M}*U*00401*{ctl}*0*{ISA_USAGE}*>")
    gs = segment("GS", "OW", SENDER_ID, RECEIVER_ID, f"{now:%Y%m%d}", f"{now:%H%M}", ctl, "X", GS_VERSION)
    content = interchange([isa, gs, *body, segment("GE", 1, ctl), segment("IEA", 1, ctl)])
    segs = segments(content)
    se = next(s for s in segs if s[0] == "SE")
    if int(se[1]) != sum(1 for s in segs if s[0] not in ("ISA", "GS", "GE", "IEA")):
        errors.append("SE count does not match the segments")
    return Built943(content=content, control=ctl, file_name=name, errors=errors, issues=issues)
