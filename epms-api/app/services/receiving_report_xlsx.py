"""The receiving report as xlsx — the sheet the warehouse used to keep by hand.

Column order matches that sheet exactly for the first fourteen columns, so an
exported range can be pasted straight into the historical workbook. The GR
number follows at the end: it is not on the old sheet, but a row nobody can
trace back to a receipt is a row nobody can settle an argument with.

Dates go in as real dates (with a display format), not strings — the whole
point of choosing xlsx over CSV is that the warehouse can sort and filter on
them without re-typing a column.
"""
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

_HEADERS = [
    "Material ID", "Description", "Manufacturer/Supplier", "Purchase Order Number",
    "Unit of Measure", "Quantity", "Department Requesting", "Requested By",
    "Date Ordered", "Arrival Date", "Left Warehouse Date", "Warehouse Receiver",
    "Person Accepting Items", "Lead time", "GR Number",
]

# Roughly the width each column needs at the data it actually holds; Excel's
# autofit is not available to a file writer, and unsized columns show
# "#######" for every date.
_WIDTHS = [12, 46, 26, 20, 10, 10, 16, 18, 13, 13, 15, 18, 20, 10, 18]

_DATE_FORMAT = "yyyy-mm-dd"
_DATE_COLUMNS = (9, 10, 11)


def build_receiving_workbook(rows: Iterable, *, date_from: date | None = None,
                             date_to: date | None = None) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Receiving Report"

    for index, header in enumerate(_HEADERS, start=1):
        cell = sheet.cell(row=1, column=index, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = _WIDTHS[index - 1]
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(_HEADERS))}1"

    row_index = 2
    for row in rows:
        values = [
            row.material_id or "",
            row.description,
            row.supplier,
            row.po_number,
            row.unit,
            # openpyxl writes Decimal as a string; a quantity column the
            # warehouse cannot sum is not worth exporting.
            float(row.quantity) if isinstance(row.quantity, Decimal) else row.quantity,
            row.department or "",
            row.requested_by or "",
            row.date_ordered,
            row.arrival_date,
            row.left_warehouse_date,
            row.warehouse_receiver or "",
            row.person_accepting or "",
            row.lead_time_days,
            row.gr_number,
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row_index, column=column, value=value)
            if column in _DATE_COLUMNS and value is not None:
                cell.number_format = _DATE_FORMAT
        row_index += 1

    # The range the numbers came from, on the sheet itself: an exported file
    # outlives the screen it was exported from.
    if date_from or date_to:
        note = sheet.cell(
            row=row_index + 1, column=1,
            value=f"Arrival date range: {date_from or 'earliest'} to {date_to or 'latest'}",
        )
        note.font = Font(italic=True)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
