"""944 warehouse stock transfer receipt advice and its allocation (Appendix
A.7; FR-OB-83, FR-OB-84).

Each W07 (one per pallet) is allocated to a transfer line of the same item,
in priority: the LX number = the line number; the line this lot already went
to; the first line of the item with quantity remaining. Repeated W07s for the
same line and lot are SUMMED, never overwritten (the A.7 sample: 14 × 600 EA).
Anything over-received, not on the transfer or unrecognised is a discrepancy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.domain.edi945 import _date, _get, _int  # same reading rules
from app.domain.x12 import X12Error, control, segments


@dataclass
class Row944:
    lx_index: int              # 1-based position of the LX in the file
    lx_number: str | None      # LX-01 as sent
    item_code: str | None      # W07-05 when W07-04 is VN
    upc: str | None            # W07-03
    qty: int
    uom: str | None
    lot: str | None = None
    description: str | None = None  # G69


@dataclass
class Receipt944:
    control: str
    transfer_key: str | None   # W17-04 (A.7), else W17-03
    receipt_date: date | None  # G62 (BS), else W17-02
    total: int | None          # W14-01
    rows: list[Row944] = field(default_factory=list)


def parse_944(data: bytes) -> Receipt944:
    segs = segments(data)
    st = next((s for s in segs if s[0] == "ST"), None)
    if st is None or _get(st, 1) != "944":
        raise X12Error("not a 944 (no ST*944)")
    r = Receipt944(control=control(_get(st, 2) or ""), transfer_key=None, receipt_date=None, total=None)
    lx_n, lx_number, row = 0, None, None
    for s in segs:
        tag = s[0]
        if tag == "W17":
            r.transfer_key = _get(s, 4) or _get(s, 3)
            r.receipt_date = _date(_get(s, 2))
        elif tag == "G62" and lx_n == 0 and _date(_get(s, 2)):
            r.receipt_date = _date(_get(s, 2))
        elif tag == "LX":
            lx_n, lx_number, row = lx_n + 1, _get(s, 1), None
        elif tag == "W07":
            if not lx_n:
                raise X12Error("W07 before any LX")
            qty = _int(_get(s, 1))
            if qty is None:
                raise X12Error(f"W07 without a quantity (LX {lx_n})")
            row = Row944(lx_index=lx_n, lx_number=lx_number, qty=qty, uom=_get(s, 2), upc=_get(s, 3),
                         item_code=_get(s, 5) if (_get(s, 4) or "").upper() == "VN" else None)
            r.rows.append(row)
        elif tag == "G69" and row is not None:
            row.description = _get(s, 1)
        elif tag == "N9" and row is not None and (_get(s, 1) or "").upper() == "LT":
            row.lot = _get(s, 2)
        elif tag == "W14":
            r.total = _int(_get(s, 1))
    if not r.transfer_key:
        raise X12Error("944 without a transfer number in W17")
    if not r.rows:
        raise X12Error("944 without W07")
    return r


# ── allocation (A.7) ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LineIn:
    line_no: int
    item_code: str
    requested: int


@dataclass
class Allocation:
    received: dict[int, int] = field(default_factory=dict)                  # line_no → EA
    by_lot: dict[tuple[int, str | None], int] = field(default_factory=dict)  # (line_no, lot) → EA (summed)
    row_line: list[int | None] = field(default_factory=list)                 # per receipt row: its line, or None
    discrepancies: list[str] = field(default_factory=list)
    line_status: dict[int, str] = field(default_factory=dict)
    outcome: str = "Waiting for 944"                                         # the transfer's status


def allocate(lines: list[LineIn], rows: list[Row944], total: int | None) -> Allocation:
    a = Allocation(received={ln.line_no: 0 for ln in lines})
    lot_line: dict[tuple[str, str], int] = {}
    unplaced: dict[str, int] = {}
    for r in rows:
        same = [ln for ln in lines if r.item_code and ln.item_code == r.item_code]
        if not same:
            a.row_line.append(None)
            key = r.item_code or (f"UPC {r.upc}" if r.upc else "an unrecognised item")
            unplaced[key] = unplaced.get(key, 0) + r.qty
            continue
        line = next((ln for ln in same if r.lx_number and str(ln.line_no) == r.lx_number.lstrip("0")), None)
        if line is None and r.lot and (r.item_code, r.lot) in lot_line:
            line = next(ln for ln in same if ln.line_no == lot_line[(r.item_code, r.lot)])
        if line is None:
            line = next((ln for ln in same if a.received[ln.line_no] < ln.requested), same[0])
        a.received[line.line_no] += r.qty
        a.by_lot[(line.line_no, r.lot)] = a.by_lot.get((line.line_no, r.lot), 0) + r.qty
        if r.lot:
            lot_line[(r.item_code, r.lot)] = line.line_no
        a.row_line.append(line.line_no)
    for ln in lines:
        got = a.received[ln.line_no]
        if got > ln.requested:
            a.line_status[ln.line_no] = "Discrepancy"
            a.discrepancies.append(f"line {ln.line_no} {ln.item_code}: {got} EA received, {ln.requested} requested")
        elif got == ln.requested:
            a.line_status[ln.line_no] = "Received"
        elif got:
            a.line_status[ln.line_no] = "Partially Received"
        else:
            a.line_status[ln.line_no] = "Open"
    for key, qty in unplaced.items():
        a.discrepancies.append(f"{key} {qty} EA received but not on the transfer")
    received_total = sum(r.qty for r in rows)
    if total is not None and total != received_total:
        a.discrepancies.append(f"the W07 lines total {received_total} EA, W14 says {total}")
    short = [f"line {ln.line_no} {ln.item_code}: {a.received[ln.line_no]} of {ln.requested} EA received"
             for ln in lines if a.received[ln.line_no] < ln.requested]
    if a.discrepancies:
        a.discrepancies += short  # the task lists every line (AC-OB-76: CF0093 0 of 7,200 EA)
        a.outcome = "Discrepancy"
    elif short and any(a.received.values()):
        a.outcome = "Partially Received"
    elif short:
        a.outcome = "Waiting for 944"
    else:
        a.outcome = "Received"
    return a
