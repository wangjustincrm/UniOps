"""Server-side XLSX builder for the Budget Dashboard partner (客商) export.

Pure function: takes already-fetched data + returns .xlsx bytes. Only depends on
openpyxl + stdlib (no DB, no app-internal imports), so it is unit-testable without
a database. Layout: per budget account a Plan row + an NC row (subtotal), then one
NC row per vendor; 12 month columns + Year; Grand Total at the bottom.
"""
from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import openpyxl
from openpyxl.styles import Font

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONEY_FMT = "#,##0.00"
_MONEY_START_COL = 6  # 1-based: cols 1..5 are Category/Code/Name/Vendor/Metric


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


def build_partner_export_xlsx(*, accounts: list[dict[str, Any]],
                              nc_monthly: dict[str, dict],
                              partners_by_account: dict[str, list[dict]],
                              fiscal_year: int, cost_center_label: str) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget vs Actual"
    bold = Font(bold=True)

    ws.append(["Budget vs Actual — Partner Breakdown"])
    ws["A1"].font = bold
    ws.append([f"FY {fiscal_year} · Cost Center: {cost_center_label}"])
    ws.append([])

    ws.append(["Category", "Account Code", "Account Name", "Vendor", "Metric",
               *_MONTHS, "Year"])
    for cell in ws[ws.max_row]:
        cell.font = bold

    gt_plan = [0.0] * 12
    gt_nc = [0.0] * 12
    gt_plan_year = 0.0
    gt_nc_year = 0.0

    def money_row(values: list, *, strong: bool = False) -> None:
        ws.append(values)
        r = ws.max_row
        for col in range(_MONEY_START_COL, len(values) + 1):
            ws.cell(row=r, column=col).number_format = _MONEY_FMT
        if strong:
            for cell in ws[r]:
                cell.font = bold

    ordered = sorted(accounts, key=lambda a: (a.get("l1_code", ""), a.get("account_code", "")))
    for a in ordered:
        aid = str(a["account_id"])
        code = a.get("account_code", "")
        name = a.get("account_name", "")
        l1 = a.get("l1_code", "")
        plan_bm = a.get("plan_by_month") or {}
        nc_bm = nc_monthly.get(aid) or {}

        plan_months = [_month(plan_bm, m) for m in range(1, 13)]
        plan_year = _num(a.get("plan_year"))
        nc_months = [_month(nc_bm, m) for m in range(1, 13)]
        nc_year = sum(nc_months)

        money_row([l1, code, name, "(subtotal)", "Plan", *plan_months, plan_year])
        money_row([l1, code, name, "(subtotal)", "NC", *nc_months, nc_year])

        for i in range(12):
            gt_plan[i] += plan_months[i]
            gt_nc[i] += nc_months[i]
        gt_plan_year += plan_year
        gt_nc_year += nc_year

        for p in partners_by_account.get(aid, []):
            pbm = p.get("by_month") or {}
            pmonths = [_month(pbm, m) for m in range(1, 13)]
            pname = p.get("partner_name") or "(no vendor)"
            money_row([l1, code, name, pname, "NC", *pmonths, sum(pmonths)])

    ws.append([])
    money_row(["Grand Total", "", "", "", "Plan", *gt_plan, gt_plan_year], strong=True)
    money_row(["Grand Total", "", "", "", "NC", *gt_nc, gt_nc_year], strong=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
