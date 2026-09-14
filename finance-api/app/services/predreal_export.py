"""Server-side XLSX builder for the Budget Dashboard export.

Pure function: takes already-fetched data + returns .xlsx bytes. Only depends on
openpyxl + stdlib (no DB, no app-internal imports), so it is unit-testable without
a database.

Layout: one row per budget account (no vendor breakdown), and each month occupies
two columns — Plan and NC posted actual — so a month reads left-to-right instead
of plan/actual living on separate rows. Year Plan / Year NC close the row, and a
Grand Total row closes the sheet.
"""
from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONEY_FMT = "#,##0.00"
_LABEL_COLS = 3          # Category / Account Code / Account Name
_MONEY_START_COL = _LABEL_COLS + 1          # first Plan column (D)
_GROUP_HEADER_ROW = 4                       # "Jan" spanning two columns
_SUB_HEADER_ROW = 5                         # "Plan" / "NC"
_TOTAL_COLS = _LABEL_COLS + 2 * (len(_MONTHS) + 1)   # +1 for the Year pair


def _num(v: Any) -> float:
    """Coerce a money value (Decimal, str, int, None) to float. '' / None -> 0.0."""
    if v is None or v == "":
        return 0.0
    return float(Decimal(str(v)))


def _month(d: dict, m: int) -> float:
    """Read month `m` from a dict keyed by int (in-process) OR str (JSON)."""
    if m in d:
        return _num(d[m])
    return _num(d.get(str(m)))


def _interleave(plan: list[float], nc: list[float]) -> list[float]:
    """[p1,p2,…], [n1,n2,…] -> [p1,n1,p2,n2,…] — the month-pair column order."""
    out: list[float] = []
    for p, n in zip(plan, nc, strict=True):
        out.append(p)
        out.append(n)
    return out


def build_budget_actual_xlsx(*, accounts: list[dict[str, Any]],
                             nc_monthly: dict[str, dict],
                             fiscal_year: int, cost_center_label: str) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget vs Actual"
    bold = Font(bold=True)
    centre = Alignment(horizontal="center", vertical="center")

    ws.append(["Budget vs Actual"])
    ws["A1"].font = bold
    ws.append([f"FY {fiscal_year} · Cost Center: {cost_center_label}"])
    ws.append([])

    # Two header rows: month name spanning its Plan/NC pair, then the pair labels.
    for col, label in enumerate(("Category", "Account Code", "Account Name"), start=1):
        cell = ws.cell(row=_GROUP_HEADER_ROW, column=col, value=label)
        cell.font = bold
        cell.alignment = centre
        ws.merge_cells(start_row=_GROUP_HEADER_ROW, start_column=col,
                       end_row=_SUB_HEADER_ROW, end_column=col)

    col = _MONEY_START_COL
    for label in (*_MONTHS, "Year"):
        group = ws.cell(row=_GROUP_HEADER_ROW, column=col, value=label)
        group.font = bold
        group.alignment = centre
        ws.merge_cells(start_row=_GROUP_HEADER_ROW, start_column=col,
                       end_row=_GROUP_HEADER_ROW, end_column=col + 1)
        for offset, sub in enumerate(("Plan", "NC")):
            cell = ws.cell(row=_SUB_HEADER_ROW, column=col + offset, value=sub)
            cell.font = bold
            cell.alignment = centre
        col += 2

    gt_plan = [0.0] * 12
    gt_nc = [0.0] * 12
    gt_plan_year = 0.0
    gt_nc_year = 0.0

    def money_row(values: list, *, strong: bool = False) -> None:
        ws.append(values)
        r = ws.max_row
        for c in range(_MONEY_START_COL, len(values) + 1):
            ws.cell(row=r, column=c).number_format = _MONEY_FMT
        if strong:
            for cell in ws[r]:
                cell.font = bold

    ordered = sorted(accounts, key=lambda a: (a.get("l1_code", ""), a.get("account_code", "")))
    for a in ordered:
        aid = str(a["account_id"])
        plan_bm = a.get("plan_by_month") or {}
        nc_bm = nc_monthly.get(aid) or {}

        plan_months = [_month(plan_bm, m) for m in range(1, 13)]
        plan_year = _num(a.get("plan_year"))
        nc_months = [_month(nc_bm, m) for m in range(1, 13)]
        nc_year = sum(nc_months)

        money_row([a.get("l1_code", ""), a.get("account_code", ""), a.get("account_name", ""),
                   *_interleave(plan_months, nc_months), plan_year, nc_year])

        for i in range(12):
            gt_plan[i] += plan_months[i]
            gt_nc[i] += nc_months[i]
        gt_plan_year += plan_year
        gt_nc_year += nc_year

    ws.append([])
    money_row(["Grand Total", "", "", *_interleave(gt_plan, gt_nc), gt_plan_year, gt_nc_year],
              strong=True)

    ws.freeze_panes = ws.cell(row=_SUB_HEADER_ROW + 1, column=_MONEY_START_COL)
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 34
    for c in range(_MONEY_START_COL, _TOTAL_COLS + 1):
        ws.column_dimensions[get_column_letter(c)].width = 13

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
