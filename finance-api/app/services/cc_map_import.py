"""Parse + resolve for the account-aware NC→UniOps cost-center map.

`_rows_from_xlsx` reads finance's `Budget vs Actual Mapping.xlsx`; it is kept
separate from `scripts/import_cc_map.py` so the parser is unit-testable and the
resolver (`resolve_uniops_cc`, added in the resolver task) lives alongside it.
"""
import openpyxl


def _rows_from_xlsx(path: str) -> list[dict]:
    """Sheet1 columns (row 1 header):
    费用类别 | Sheet Name | Account in ERP | 部门 | 成本中心(NC) | 成本中心(UniOps)

    'Account in ERP' only appears on each category's first row -> forward-fill.
    Rows with no 部门 or no UniOps CC are skipped (blank/spacer rows). Blank
    成本中心(NC) -> 'ALL' wildcard.
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Sheet1"]
    out: list[dict] = []
    account = None
    for i, row in enumerate(ws.iter_rows(values_only=True), 1):
        if i == 1:
            continue  # header
        _cat, _sheet, acct, dept, nc_cc, uni_cc = (list(row) + [None] * 6)[:6]
        if acct not in (None, ""):
            account = str(int(acct)) if isinstance(acct, float) else str(acct).strip()
        if dept in (None, "") or uni_cc in (None, ""):
            continue
        out.append({
            "account_code": account,
            "dept_code": str(dept).strip(),
            "nc_cc_code": str(nc_cc).strip() if nc_cc not in (None, "") else "ALL",
            "uniops_cc_code": str(uni_cc).strip(),
        })
    return out
