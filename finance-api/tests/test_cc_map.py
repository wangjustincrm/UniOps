"""Tests for the account-aware NC->UniOps cost-center map (parser + resolver)."""
import openpyxl

from app.services.cc_map_import import _rows_from_xlsx, resolve_uniops_cc


def _make_xlsx(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["费用类别", "Sheet Name", "Account in ERP", "部门", "成本中心(NC)", "成本中心(UniOps)"])
    ws.append(["制造费用", "ENG", 5101, "0106", "ALL", "MOH-0106-E01"])
    ws.append([None, "P02", None, "0104", "P02", "MOH-0104-P02"])
    ws.append(["研发费用", "R&D", 5301, "ALL", "ALL", "RD-0109"])
    ws.append([None, "blankrow", None, None, None, None])  # skipped (no dept/uni)
    p = tmp_path / "map.xlsx"
    wb.save(p)
    return str(p)


def test_rows_forward_fill_account_and_skip_blank(tmp_path):
    rows = _rows_from_xlsx(_make_xlsx(tmp_path))
    # 'Account in ERP' forward-fills to the P02 row (5101) and the R&D row (5301)
    assert {r["account_code"] for r in rows} == {"5101", "5301"}
    p02 = next(r for r in rows if r["nc_cc_code"] == "P02")
    assert p02 == {"account_code": "5101", "dept_code": "0104",
                   "nc_cc_code": "P02", "uniops_cc_code": "MOH-0104-P02"}
    # blank spacer row dropped; every kept row has dept + uniops cc
    assert all(r["dept_code"] and r["uniops_cc_code"] for r in rows)
    # blank 成本中心(NC) becomes 'ALL'
    assert next(r for r in rows if r["account_code"] == "5301")["nc_cc_code"] == "ALL"


# ── resolver: account-aware 3-tier ────────────────────────────────────────────
_ROWS = [
    {"account_code": "5101", "dept_code": "0106", "nc_cc_code": "ALL", "uniops_cc_code": "MOH-0106-E01"},
    {"account_code": "5101", "dept_code": "0104", "nc_cc_code": "P02", "uniops_cc_code": "MOH-0104-P02"},
    {"account_code": "6602", "dept_code": "0108", "nc_cc_code": "ALL", "uniops_cc_code": "GA-0100"},
    {"account_code": "5301", "dept_code": "ALL", "nc_cc_code": "ALL", "uniops_cc_code": "RD-0109"},
    {"account_code": "6603", "dept_code": "ALL", "nc_cc_code": "ALL", "uniops_cc_code": "FN-0103"},
]


def test_exact_code_wins():
    assert resolve_uniops_cc(_ROWS, "5101", "0104", "P02") == "MOH-0104-P02"


def test_dept_all_fallback():
    assert resolve_uniops_cc(_ROWS, "5101", "0106", "SOMECODE") == "MOH-0106-E01"


def test_account_all_all():
    assert resolve_uniops_cc(_ROWS, "5301", "0999", "X") == "RD-0109"
    assert resolve_uniops_cc(_ROWS, "6603", "0103", None) == "FN-0103"


def test_unmapped_returns_none():
    # 6602 + 0106 intentionally absent -> engineering-in-management is an exception
    assert resolve_uniops_cc(_ROWS, "6602", "0106", "ALL") is None


def test_account_aware_same_dept_differs_by_account():
    assert resolve_uniops_cc(_ROWS, "6602", "0108", "ALL") == "GA-0100"
    assert resolve_uniops_cc(_ROWS, "5101", "0108", "ALL") is None  # no 5101/0108 row
