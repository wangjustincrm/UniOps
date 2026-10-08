"""Turn a controlled-query result into a workbook.

The model is not in this path. It chose the query, the person read the answer
and decided it was right, and only then does anything get written — so a figure
in the file is one that already came out of the database and was checked on
screen. Nothing here can invent a number.

Two things the sheet carries beyond the data, both so a file that outlives the
conversation can still be trusted:

  * the question it came from and the query that ran, on a header block. A
    spreadsheet that lands in someone's inbox a week later is otherwise a grid
    of numbers with no provenance.
  * an explicit note when the row cap was hit. A report that is quietly missing
    half its rows is the failure this whole layer exists to avoid, and it is
    invisible in a spreadsheet in a way it is not on screen.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

_HEADER_FILL = PatternFill("solid", fgColor="E8EFEE")
_TITLE_FONT = Font(bold=True, size=13)
_META_FONT = Font(size=9, color="6B7679")
_HEAD_FONT = Font(bold=True, size=10)
_TOTAL_FONT = Font(bold=True)
_MONEY_FMT = "#,##0.00"

# Wide enough to read, narrow enough to print. Excel cannot autofit from a file
# writer, and an unsized date column shows "#######".
_MIN_W, _MAX_W = 10, 46


def _looks_numeric(value) -> Decimal | None:
    """Money and counts arrive as strings to preserve precision; put them back
    as numbers so the recipient can sum and sort without re-typing a column."""
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except (InvalidOperation, ValueError):
            return None
    return None


def _pretty(label: str, headers: dict[str, str]) -> str:
    """Column heading a person would recognise.

    Falls back to the raw key rather than guessing: a header of
    "order.originating_pr.department_name" is ugly but honest, and tells whoever
    opens the file exactly which field it is.
    """
    return headers.get(label, label)


def build_workbook(*, question: str, entity_label: str, query: dict,
                   rows: list[dict], labels: list[str],
                   headers: dict[str, str], totals: dict | None,
                   truncated: bool, row_cap: int,
                   generated_for: str | None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Report"

    width = max(1, len(labels))

    ws.cell(row=1, column=1, value=question.strip() or entity_label).font = _TITLE_FONT
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=width)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta = f"{entity_label} · generated {stamp}"
    if generated_for:
        meta += f" · for {generated_for}"
    ws.cell(row=2, column=1, value=meta).font = _META_FONT
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=width)

    # The query, verbatim. Whoever receives this file can check what it asked.
    summary = ", ".join(
        f"{k}={v}" for k, v in query.items() if v not in (None, [], {}, "")
    )
    ws.cell(row=3, column=1, value=f"Query: {summary}").font = _META_FONT
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=width)

    head_row = 5
    if truncated:
        # Said out loud, at the top, in the file itself. On screen a capped
        # result is visible; in a spreadsheet it is a complete-looking report
        # that happens to stop early.
        warn = (f"⚠ Showing the first {row_cap:,} rows — the full result is "
                f"larger. Narrow the question to get all of it.")
        c = ws.cell(row=4, column=1, value=warn)
        c.font = Font(bold=True, color="9C3A30")
        ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=width)
    else:
        head_row = 4

    for col, key in enumerate(labels, start=1):
        c = ws.cell(row=head_row, column=col, value=_pretty(key, headers))
        c.font = _HEAD_FONT
        c.fill = _HEADER_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)

    for i, row in enumerate(rows, start=head_row + 1):
        for col, key in enumerate(labels, start=1):
            raw = row.get(key)
            number = _looks_numeric(raw)
            if number is not None:
                c = ws.cell(row=i, column=col, value=number)
                if number != number.to_integral_value():
                    c.number_format = _MONEY_FMT
            else:
                ws.cell(row=i, column=col, value=raw)

    if totals:
        r = head_row + len(rows) + 2
        ws.cell(row=r, column=1, value="Total").font = _TOTAL_FONT
        for col, key in enumerate(labels, start=1):
            if key not in totals:
                continue
            number = _looks_numeric(totals[key])
            c = ws.cell(row=r, column=col,
                        value=number if number is not None else totals[key])
            c.font = _TOTAL_FONT
            if number is not None and number != number.to_integral_value():
                c.number_format = _MONEY_FMT
        ws.cell(row=r + 1, column=1,
                value="Totals are computed over the whole result, not the rows "
                      "shown above.").font = _META_FONT

    for col, key in enumerate(labels, start=1):
        longest = max(
            [len(_pretty(key, headers))]
            + [len(str(r.get(key) or "")) for r in rows[:200]]
        )
        ws.column_dimensions[get_column_letter(col)].width = min(
            max(longest + 2, _MIN_W), _MAX_W)

    ws.freeze_panes = ws.cell(row=head_row + 1, column=1)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filename_for(entity: str) -> str:
    return f"{entity.replace('_', '-')}-{date.today()}.xlsx"
