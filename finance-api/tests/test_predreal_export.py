import io

import openpyxl

from app.services.predreal_export import build_budget_actual_xlsx


def _rows(data: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(data))
    return list(wb.active.iter_rows(values_only=True))


def _find(rows, code):
    for r in rows:
        if r and r[1] == code:
            return r
    raise AssertionError(f"row not found: {code}")


def _sample():
    accounts = [
        {"account_id": "a1", "account_code": "5101010", "account_name": "Freight",
         "l1_code": "5101", "plan_by_month": {"1": "100.00", "2": "100.00"}, "plan_year": "1200.00"},
        {"account_id": "a2", "account_code": "6601010", "account_name": "Ads",
         "l1_code": "6601", "plan_by_month": {}, "plan_year": "0"},
    ]
    nc_monthly = {"a1": {1: "80.00", 2: "90.00"}}
    return accounts, nc_monthly


def _build():
    accounts, nc_monthly = _sample()
    return build_budget_actual_xlsx(accounts=accounts, nc_monthly=nc_monthly,
                                    fiscal_year=2026, cost_center_label="All Cost Centers")


def test_header_is_month_pairs():
    rows = _rows(_build())
    group = next(r for r in rows if r and r[0] == "Category")
    sub = rows[rows.index(group) + 1]
    # Labels occupy the first three columns; month names span their Plan/NC pair.
    assert group[:3] == ("Category", "Account Code", "Account Name")
    assert group[3] == "Jan" and group[5] == "Feb" and group[25] == "Dec" and group[27] == "Year"
    # merged cells leave the second column of each pair empty on the group row
    assert group[4] is None and group[26] is None and group[28] is None
    assert sub[3:9] == ("Plan", "NC", "Plan", "NC", "Plan", "NC")
    assert sub[27:29] == ("Plan", "NC")
    assert len(group) == 29


def test_account_row_carries_plan_and_nc_side_by_side():
    rows = _rows(_build())
    r = _find(rows, "5101010")
    assert r[0] == "5101" and r[2] == "Freight"
    assert r[3] == 100.0 and r[4] == 80.0      # Jan plan / Jan NC
    assert r[5] == 100.0 and r[6] == 90.0      # Feb plan / Feb NC
    assert r[27] == 1200.0 and r[28] == 170.0  # Year plan / Year NC


def test_one_row_per_account_no_vendor_breakdown():
    rows = _rows(_build())
    data_rows = [r for r in rows if r and r[1] in ("5101010", "6601010")]
    assert len(data_rows) == 2
    # no "(subtotal)" / vendor-name column survives anywhere in the sheet
    flat = {c for r in rows for c in r if isinstance(c, str)}
    assert "(subtotal)" not in flat and "Vendor" not in flat and "Metric" not in flat


def test_grand_total():
    rows = _rows(_build())
    gt = next(r for r in rows if r and r[0] == "Grand Total")
    assert gt[3] == 100.0 and gt[4] == 80.0
    assert gt[27] == 1200.0 and gt[28] == 170.0
