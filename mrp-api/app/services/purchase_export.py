"""Purchase suggestions as xlsx — what a buyer actually works from.

One row per suggestion, ordered by order date, because the sheet is used to
answer "what has to be placed this week" and re-sorting a spreadsheet is a
step people skip.

The flags come across as words in their own columns rather than colours: a
sheet gets filtered, mailed and pasted into other sheets, and colour does
not survive any of that. "no supplier" in a cell does.
"""
from decimal import Decimal
from io import BytesIO
from typing import Iterable, Protocol

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font


class _LineLike(Protocol):
    material_code: str
    need_week: object
    order_date: object
    gross_qty: Decimal
    available_qty: Decimal
    net_qty: Decimal
    suggested_qty: Decimal
    raised_to_moq: Decimal
    partner_code: str | None
    lead_time_days: int | None
    supplier_missing: bool
    lead_time_missing: bool
    order_date_passed: bool
    status: str


_HEADERS = [
    "Order by", "Need week", "Material", "Supplier", "Lead time (days)",
    "Gross", "Available", "Net", "Suggested", "Added for MOQ", "Status", "Attention",
]


def _attention(line: _LineLike) -> str:
    """Everything a buyer must resolve before acting on the row, spelled
    out. Empty when the row is ready to order as it stands."""
    notes = []
    if line.order_date_passed:
        notes.append("order date passed")
    if line.supplier_missing:
        notes.append("no supplier")
    if line.lead_time_missing:
        notes.append("no lead time")
    return "; ".join(notes)


def build_purchase_workbook(run, lines: Iterable[_LineLike]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = run.run_no[:31]          # Excel caps sheet names at 31 chars

    for column, header in enumerate(_HEADERS, start=1):
        cell = sheet.cell(row=1, column=column, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    row_index = 2
    for line in sorted(lines, key=lambda l: (l.order_date, l.material_code)):
        values = [
            line.order_date, line.need_week, line.material_code,
            line.partner_code or "", line.lead_time_days,
            float(line.gross_qty), float(line.available_qty), float(line.net_qty),
            float(line.suggested_qty), float(line.raised_to_moq),
            line.status, _attention(line),
        ]
        for column, value in enumerate(values, start=1):
            sheet.cell(row=row_index, column=column, value=value)
        row_index += 1

    # A note row under the data, not a separate sheet nobody opens: the two
    # things these numbers do NOT account for belong next to them.
    note_row = row_index + 1
    sheet.cell(row=note_row, column=1, value=(
        f"Loss rates applied: raw {run.raw_material_loss_rate}, "
        f"packaging {run.packaging_loss_rate}. In-transit stock is not counted — "
        f"these are requirements, not a net position against what is already on order."
    ))

    widths = [12, 12, 16, 14, 16, 12, 12, 12, 12, 14, 10, 30]
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=column).column_letter].width = width

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
