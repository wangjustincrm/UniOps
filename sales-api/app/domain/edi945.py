"""945 warehouse shipping advice (Appendix A.6; FR-OB-25b, FR-OB-28).

Every W12 under every LX is one row — never only the first (FR-OB-25b). The
pallet's SSCC (`MAN*GM`) stays a string with its leading zeros. Radiant works
in EA, so W12 quantities are EA.

VERIFIED AGAINST REAL FILES (2026-11-03: Radiant's 11 real 945s on dev2 —
nine Shopify orders, one Zoho manual order "C0031", one Sobeys order; the
anonymised shape is tests/fixtures/radiant_945_real_shape.edi):
- W06: W06-02 the order key (Shopify order number; Zoho's own number for a
  retailer or manual order, e.g. "001008597", "C0031"), W06-04 Radiant's
  shipment id, W06-06 the PO (the Shopify number again, the retailer PO, or
  absent for a manual order). Header `N9*PO` repeats the PO; header `N9*E1` is
  Zoho's reference ("SHP-9497", or its 940 file name
  "CRM-940_<number>_<partner>_<PO>"). Both are kept (`po`, `reference`).
- header: N1*ST (name, 94 id) + N3 + N4; `N9*MB` and `N9*BM` (master bill,
  bill of lading); the ship date is `G62*11` (no DTM in the real files — DTM
  is still read first when present); W27-01 the mode (U parcel, LT LTL),
  W27-02 the carrier (PRLA, WIFS).
- an LX is a PALLET (`LX` + `MAN*GM` SSCC + `PAL` weight), not the 940's line:
  several W12s follow one LX. `N9*PKG` repeats the SSCC.
- W12: W12-01 "CC", W12-02 ordered, W12-03 shipped, W12-05 "EA", W12-06 the
  UPC, W12-07 "VN", W12-08 our item code; W12-09 / W12-10 empty.
- after each W12: `G69` (description), `N9*LT` lot, `N9*2I` the parcel's
  tracking (per W12, not per pallet), `N9*PKG`, `N9*E1/E2/E3` (description,
  case GTIN, buyer part), and the expiry as `G62*36` — not `DTM*036` (still
  read); "19000101" means none. An item without a lot (CRMMARK002) has no
  `N9*LT` and the 19000101 expiry.
- every file is one ST per order; W03 holds the totals.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from app.domain.x12 import X12Error, control, segments


@dataclass
class Party:
    name: str | None = None
    id_qualifier: str | None = None
    id: str | None = None
    address1: str | None = None
    city: str | None = None
    province: str | None = None
    zip: str | None = None
    country: str | None = None


@dataclass
class Row945:
    lx_index: int            # 1-based position of the LX in the set
    w12_index: int           # 1-based position of the W12 under its LX
    lx_number: str | None    # LX-01 as sent (the 940 line, FR-OB-25a)
    sscc: str | None
    status: str | None       # W12-01
    qty_ordered: int | None
    qty_shipped: int
    uom: str | None
    upc: str | None
    item_code: str | None    # W12-08 when W12-07 is VN
    lot: str | None = None
    expiry: date | None = None
    weight: Decimal | None = None
    tracking: str | None = None  # the parcel's N9*2I (after its W12), else the pallet's


@dataclass
class Shipment945:
    control: str                 # ST02, leading zeros dropped
    group_control: str | None
    order_key_raw: str | None    # W06-02, else W06-06 — as received (FR-OB-28)
    po: str | None               # W06-06
    shipment_id: str | None      # W06-04
    ship_date: date | None
    ship_date_source: str | None  # "DTM" | "G62"
    mode: str | None = None
    carrier: str | None = None
    tracking: list[str] = field(default_factory=list)
    carrier_ref: str | None = None   # N9*MB
    bol: str | None = None           # N9*BM
    reference: str | None = None     # header N9*E1: Zoho's reference (its 940 name, or "SHP-<n>")
    parties: dict[str, Party] = field(default_factory=dict)  # N1-01 → party
    rows: list[Row945] = field(default_factory=list)


NO_DATE = "19000101"  # Radiant's "no expiry" (an item without a lot)


def _date(v: str | None) -> date | None:
    v = (v or "").strip()
    if v == NO_DATE:
        return None
    if re.fullmatch(r"\d{8}", v):
        try:
            return date(int(v[:4]), int(v[4:6]), int(v[6:]))
        except ValueError:
            return None
    if re.fullmatch(r"\d{6}", v):
        try:
            return date(2000 + int(v[:2]), int(v[2:4]), int(v[4:]))
        except ValueError:
            return None
    return None


def _int(v: str | None) -> int | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        d = Decimal(v)
    except InvalidOperation as e:
        raise X12Error(f"not a quantity: {v!r}") from e
    if d != d.to_integral_value():
        raise X12Error(f"a fractional EA quantity: {v!r}")
    return int(d)


def _get(s: list[str], i: int) -> str | None:
    return (s[i].strip() or None) if len(s) > i else None


def parse_945(data: bytes) -> list[Shipment945]:
    """Each ST*945 set in the file (one per order, D18)."""
    segs = segments(data)
    gs06 = next((control(s[6]) for s in segs if s[0] == "GS" and len(s) > 6), None)
    out: list[Shipment945] = []
    cur: Shipment945 | None = None
    header_dtm: list[tuple[str | None, date]] = []
    header_g62: list[tuple[str | None, date]] = []
    lx_n = 0
    sscc: str | None = None
    lx_number: str | None = None
    pallet_tracking: str | None = None
    row: Row945 | None = None
    party: Party | None = None
    for s in segs:
        tag = s[0]
        if tag == "ST":
            if _get(s, 1) != "945":
                cur = None
                continue
            cur = Shipment945(control=control(_get(s, 2) or ""), group_control=gs06, order_key_raw=None, po=None,
                              shipment_id=None, ship_date=None, ship_date_source=None)
            out.append(cur)
            header_dtm, header_g62, lx_n, sscc, lx_number, pallet_tracking, row, party = [], [], 0, None, None, None, None, None
            continue
        if cur is None:
            continue
        if tag == "SE":
            _finish(cur, header_dtm, header_g62)
            cur = None
        elif tag == "W06":
            cur.order_key_raw = _get(s, 2) or _get(s, 6)
            cur.po, cur.shipment_id = _get(s, 6), _get(s, 4)
        elif tag == "N1":
            party = Party(name=_get(s, 2), id_qualifier=_get(s, 3), id=_get(s, 4))
            cur.parties.setdefault(_get(s, 1) or "", party)
        elif tag == "N3" and party is not None:
            party.address1 = _get(s, 1)
        elif tag == "N4" and party is not None:
            party.city, party.province, party.zip, party.country = _get(s, 1), _get(s, 2), _get(s, 3), _get(s, 4)
        elif tag == "W27":
            cur.mode, cur.carrier = _get(s, 1), _get(s, 2)
        elif tag == "LX":
            lx_n += 1
            lx_number, sscc, pallet_tracking, row, party = _get(s, 1), None, None, None, None
        elif tag == "MAN" and lx_n and _get(s, 1) == "GM":
            sscc = _get(s, 2)  # a string: leading zeros kept (AC-OB-35)
            for r in cur.rows:
                if r.lx_index == lx_n and r.sscc is None:
                    r.sscc = sscc
        elif tag == "W12":
            if not lx_n:
                raise X12Error("W12 before any LX")
            shipped = _int(_get(s, 3))
            if shipped is None:
                raise X12Error(f"W12 without a shipped quantity (LX {lx_n})")
            row = Row945(lx_index=lx_n, w12_index=sum(1 for r in cur.rows if r.lx_index == lx_n) + 1,
                         lx_number=lx_number, sscc=sscc, status=_get(s, 1), qty_ordered=_int(_get(s, 2)),
                         qty_shipped=shipped, uom=_get(s, 5), upc=_get(s, 6),
                         item_code=_get(s, 8) if (_get(s, 7) or "").upper() == "VN" else None,
                         lot=_get(s, 9), tracking=pallet_tracking)
            w = _get(s, 10)
            try:
                row.weight = Decimal(w) if w else None
            except InvalidOperation:
                row.weight = None
            cur.rows.append(row)
        elif tag == "N9":
            q, v = (_get(s, 1) or "").upper(), _get(s, 2)
            if q == "LT" and row is not None:
                row.lot = v
            elif q == "2I":
                if row is not None:  # after a W12: that parcel's (the real files)
                    row.tracking = v
                elif lx_n:  # after the LX, before its first W12: the pallet's
                    pallet_tracking = v
                if v and v not in cur.tracking:
                    cur.tracking.append(v)
            elif q == "MB" and not lx_n:
                cur.carrier_ref = v
            elif q == "BM" and not lx_n:
                cur.bol = v
            elif q == "PO" and not lx_n and v:
                cur.po = cur.po or v
            elif q == "E1" and not lx_n:
                cur.reference = v
        elif tag == "DTM":
            d = _date(_get(s, 2))
            if lx_n == 0:
                if d is not None:
                    header_dtm.append((_get(s, 1), d))
            elif row is not None and _get(s, 1) == "036":
                row.expiry = d
        elif tag == "G62":
            d = _date(_get(s, 2))
            if lx_n == 0:
                if d is not None:
                    header_g62.append((_get(s, 1), d))
            elif row is not None and _get(s, 1) == "36":  # the real files' expiry (19000101: none)
                row.expiry = d
    if cur is not None:
        raise X12Error("945 set without SE")
    if not out:
        raise X12Error("not a 945 (no ST*945)")
    for sh in out:
        if not sh.order_key_raw:
            raise X12Error(f"945 {sh.control}: no order key in W06-02 or W06-06")
        if not sh.rows:
            raise X12Error(f"945 {sh.control}: no W12")
    return out


def _finish(sh: Shipment945, dtm: list[tuple[str | None, date]], g62: list[tuple[str | None, date]]) -> None:
    shipped = next((d for q, d in dtm if q == "011"), None) or (dtm[0][1] if dtm else None)
    if shipped is not None:
        sh.ship_date, sh.ship_date_source = shipped, "DTM"
    elif g62:  # G62*11 "shipped on" (the real files), else the first
        sh.ship_date, sh.ship_date_source = next((d for q, d in g62 if q == "11"), g62[0][1]), "G62"


# ── FR-OB-28: the order key ──────────────────────────────────────────────────

@dataclass(frozen=True)
class OrderKey:
    received: str
    base: str | None         # what is looked up; None → review
    suffix: str | None       # the one letter stripped
    numeric: bool            # numeric keys compare without leading zeros


@dataclass(frozen=True)
class ZohoReference:
    """Header `N9*E1` of a 945 that answers a Zoho 940: its file name."""
    warehouse_order: str
    partner_name: str
    po: str


def zoho_reference(value: str | None) -> ZohoReference | None:
    """`CRM-940_<warehouse order no>_<partner name>_<PO>`; None for anything else
    ("SHP-9497", nothing). A partner name may hold an underscore, the two ends not."""
    m = re.fullmatch(r"CRM-940_([^_]+)_(.+)_([^_]+)", (value or "").strip())
    return ZohoReference(m[1], m[2], m[3]) if m else None


def order_key(raw: str) -> OrderKey:
    """`1000123a` → `1000123` (exactly ONE trailing letter); zero-padding
    dropped (`000001234` → `1234`). Digits followed by two or more letters →
    no base (review, AC-OB-78). Any other value (a retailer PO such as
    `W-2`) is looked up as it is."""
    v = raw.strip()
    if re.fullmatch(r"\d+[A-Za-z]{2,}", v) or not v:
        return OrderKey(raw, None, None, False)
    if re.fullmatch(r"\d+[A-Za-z]", v):
        return OrderKey(raw, str(int(v[:-1])), v[-1], True)
    if re.fullmatch(r"\d+", v):
        return OrderKey(raw, str(int(v)), None, True)
    return OrderKey(raw, v, None, False)
