"""Radiant's inventory report (its portal's export, user 2026-11-13): what the warehouse holds, by item and lot,
read from the file as it comes — CSV or Excel (.xlsx), the per-pallet detail or the per-lot summary.

Columns by their header (any order, case and spacing ignored):
  item     "Display Item Number" (empty or missing: "Item Number", its "CRM|" client prefix taken off)
  lot      "Lot Number" (empty: an item with no lot, e.g. CHEP pallets)
  expiry   "Expiration Date" (1900-01-01, Radiant's "none", is none)
  quantity "Total Quantity" (summary) or "Actual Quantity" (detail), in "Uom" EA
  hold     "Status" ON-HOLD / H, or a "Current Holds" entry: that quantity is on hold
Rows of the same item and lot add up. Excel is read with the standard library (an .xlsx is zipped XML)."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

NO_EXPIRY = date(1900, 1, 1)
HOLD = {"ON-HOLD", "ONHOLD", "ON HOLD", "H", "HOLD", "HELD"}


class StockFileError(ValueError):
    """The file is not a Radiant inventory report Sales can read."""


@dataclass
class CountRow:
    item_code: str
    lot: str | None
    expiry: date | None
    qty_ea: int
    hold_ea: int
    description: str | None


def _key(h: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def _xlsx_rows(data: bytes) -> list[list[object]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            raise StockFileError("the Excel file has no sheet")
        root = ElementTree.fromstring(z.read(sheets[0]))
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError) as e:
        raise StockFileError(f"the Excel file cannot be read ({type(e).__name__})") from e
    out: list[list[object]] = []
    for row in root.iter(f"{{{ns['m']}}}row"):
        cells: dict[int, object] = {}
        for c in row.findall("m:c", ns):
            ref = re.match(r"([A-Z]+)", c.get("r") or "")
            col = 0
            for ch in (ref.group(1) if ref else ""):
                col = col * 26 + ord(ch) - 64
            t, v = c.get("t"), c.find("m:v", ns)
            if t == "s" and v is not None:
                val: object = shared[int(v.text or 0)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t"))
            elif v is not None and v.text is not None:
                val = v.text if t in ("str", "e", "b") else _number(v.text)
            else:
                val = None
            cells[col - 1 if col else len(cells)] = val
        if cells:
            out.append([cells.get(i) for i in range(max(cells) + 1)])
    return out


def _number(text: str) -> object:
    try:
        d = Decimal(text)
    except InvalidOperation:
        return text
    return int(d) if d == d.to_integral_value() else d


def _csv_rows(data: bytes) -> list[list[object]]:
    text = data.decode("utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t") if text.strip() else csv.excel
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def _date(v: object) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, Decimal)) and not isinstance(v, bool):
        d = date(1899, 12, 30) + timedelta(days=int(v))  # an Excel date number
    else:
        s = str(v).strip()
        d = None
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                d = datetime.strptime(s[:19] if " " in s else s, fmt).date()
                break
            except ValueError:
                continue
        if d is None:
            raise StockFileError(f"an expiry date cannot be read: {s!r}")
    return None if d <= NO_EXPIRY else d


def _qty(v: object, where: str) -> int:
    try:
        d = Decimal(str(v).replace(",", "").strip() or "0")
    except InvalidOperation as e:
        raise StockFileError(f"{where}: the quantity {v!r} is not a number") from e
    if d != d.to_integral_value():
        raise StockFileError(f"{where}: the quantity {v} is not a whole number of EA")
    return int(d)


def parse_stock_file(name: str, data: bytes) -> list[CountRow]:
    """The report's rows by item and lot (quantities added up), or StockFileError saying what is wrong."""
    if not data:
        raise StockFileError("the file is empty")
    rows = _xlsx_rows(data) if data[:2] == b"PK" or name.lower().endswith(".xlsx") else _csv_rows(data)
    head_at = next((i for i, r in enumerate(rows[:20]) if {"lotnumber"} <= {_key(c) for c in r}), None)
    if head_at is None:
        raise StockFileError('no "Lot Number" column: is this Radiant\'s inventory report?')
    head = {_key(c): i for i, c in enumerate(rows[head_at]) if _key(c)}

    def col(*names: str) -> int | None:
        return next((head[n] for n in names if n in head), None)

    c_item, c_item2 = col("displayitemnumber", "itemnumber"), col("itemnumber")
    c_lot, c_exp = col("lotnumber"), col("expirationdate", "expirydate")
    c_qty, c_uom = col("totalquantity", "actualquantity", "quantity"), col("uom")
    c_status, c_holds, c_desc = col("status"), col("currentholds"), col("description", "itemdescription")
    if c_item is None or c_qty is None:
        raise StockFileError('the item ("Display Item Number") or quantity ("Total Quantity") column is missing')
    out: dict[tuple[str, str | None], CountRow] = {}
    for n, r in enumerate(rows[head_at + 1:], start=head_at + 2):
        cell = lambda i: (r[i] if i is not None and i < len(r) else None)  # noqa: E731
        code = str(cell(c_item) or "").strip() or str(cell(c_item2) or "").strip()
        if "|" in code:
            code = code.split("|", 1)[1].strip()
        if not code:
            continue
        where = f"row {n} ({code})"
        uom = str(cell(c_uom) or "EA").strip().upper()
        if uom not in ("EA", ""):
            raise StockFileError(f"{where}: unit {uom}, Sales counts EA")
        lot = str(cell(c_lot) or "").strip() or None
        qty = _qty(cell(c_qty), where)
        held = str(cell(c_status) or "").strip().upper() in HOLD or bool(str(cell(c_holds) or "").strip())
        k = (code, lot)
        row = out.get(k) or CountRow(code, lot, None, 0, 0, str(cell(c_desc) or "").strip() or None)
        row.qty_ea += qty
        row.hold_ea += qty if held else 0
        exp = _date(cell(c_exp))
        row.expiry = max(filter(None, (row.expiry, exp)), default=None)
        out[k] = row
    if not out:
        raise StockFileError("the report has no rows")
    return list(out.values())
