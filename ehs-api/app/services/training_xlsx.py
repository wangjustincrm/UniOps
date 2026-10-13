"""The training register as a spreadsheet.

Round 5, TC-08: "provide a way to export the report to a xls file."

A real workbook rather than a CSV named `.xls`. The reason is not pedantry: the
register is full of names and course titles with commas and accents in them, and
Excel's CSV import guesses at the encoding — ask anybody who has opened a French
name in it. A workbook carries its own types too, so a completion date sorts as a
date and not as text.

★ The filter is on the sheet, in the first rows, for the same reason it is on the
printed page: "these twelve people are trained" and "these twelve records match a
search for forklift in March" are different claims, and a bare grid of rows gets
read as the first. A spreadsheet is also the thing somebody mails on, detached
from the screen it came from.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADINGS = ("Person", "Course", "Completed", "Expires", "Length (minutes)", "Run by",
            "Instructor", "Entered by")
WIDTHS = (28, 34, 13, 13, 16, 24, 24, 24)

HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)

# ★★ Excel treats a cell beginning with any of these as a formula, and openpyxl
# writes it through as one. Everything on this sheet that a person typed — an
# instructor's name, the search text in the filter line — would be evaluated on
# open: `=HYPERLINK("http://elsewhere","Your certificate")` in the instructor
# field becomes a live link in a spreadsheet that came from us, and an ordinary
# name starting with "=" becomes a broken formula instead of text.
#
# Raised in review by uniops-41. UniOps has been here before: Budget Actual's
# export silently dropped text beginning with "=" for the same reason.
_FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")


def _put(cell, value):
    """Write a value and make sure Excel reads it as what it is.

    ★ Measured rather than assumed, after uniops-41 pointed out that the obvious
    fix is wrong: prefixing with an apostrophe, which is the convention when
    *typing* into Excel, makes openpyxl store the apostrophe — the sheet then
    shows `'=1+1`. Forcing the cell to a string stores the original text as an
    inline string, which is not a formula and reads correctly; `quotePrefix`
    tells Excel the text form is deliberate so it does not offer to convert it.

    Only strings are touched. A date has to stay a date or the column stops
    sorting as one, which is half the reason this is a workbook.
    """
    cell.value = value
    if isinstance(value, str) and value.startswith(_FORMULA_STARTERS):
        cell.data_type = "s"
        cell.quotePrefix = True
    return cell


def render(*, rows: list[dict], filters: list[str], printed_by: str | None,
           title: str = "Training records") -> bytes:
    book = Workbook()
    sheet = book.active
    # Excel refuses a sheet name over 31 characters or carrying []:*?/\\ — and
    # it refuses the whole file, not just the name.
    sheet.title = title[:31]

    _put(sheet["A1"], title).font = Font(bold=True, size=13)

    stamp = datetime.now(timezone.utc).astimezone().strftime("%d %b %Y, %H:%M")
    tail = f"{len(rows)} record{'' if len(rows) == 1 else 's'} · exported {stamp}"
    if printed_by:
        tail = f"{tail} · by {printed_by}"
    _put(sheet["A2"], tail).font = Font(size=9, color="6B7280")

    row_at = 3
    for line in (filters or ["Every record on file, unfiltered."]):
        _put(sheet.cell(row=row_at, column=1),
             line).font = Font(size=9, color="6B7280")
        row_at += 1

    row_at += 1
    head = row_at
    for col, heading in enumerate(HEADINGS, start=1):
        cell = sheet.cell(row=head, column=col, value=heading)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        sheet.column_dimensions[get_column_letter(col)].width = WIDTHS[col - 1]

    for offset, r in enumerate(rows, start=1):
        values = (
            r["user_name"], r["course_label"],
            # Real dates, so the column sorts and filters as dates in Excel.
            r["completed_on"], r["expires_on"],
            # A number, so it adds up in Excel; empty where it is not known.
            r.get("length_minutes"),
            r["run_by_name"] or "—", r["instructor_name"] or "—",
            r["recorded_by_name"] or "—",
        )
        for col, value in enumerate(values, start=1):
            cell = _put(sheet.cell(row=head + offset, column=col), value)
            if col in (3, 4) and value is not None and not isinstance(value, str):
                cell.number_format = "yyyy-mm-dd"

    # The headings stay visible on a register somebody scrolls, and the filter
    # row gives the columns their drop-downs.
    sheet.freeze_panes = sheet.cell(row=head + 1, column=1)
    sheet.auto_filter.ref = (
        f"A{head}:{get_column_letter(len(HEADINGS))}{head + len(rows)}")

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
