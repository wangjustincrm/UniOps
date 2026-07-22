import io

import openpyxl

from app.services.predreal_export import build_partner_export_xlsx


def _rows(data: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(data))
    return list(wb.active.iter_rows(values_only=True))


def _find(rows, code, metric, vendor=None):
    for r in rows:
        if r and r[1] == code and r[4] == metric and (vendor is None or r[3] == vendor):
            return r
    raise AssertionError(f"row not found: {code} {metric} {vendor}")


def _sample():
    accounts = [
        {"account_id": "a1", "account_code": "5101010", "account_name": "Freight",
         "l1_code": "5101", "plan_by_month": {"1": "100.00", "2": "100.00"}, "plan_year": "1200.00"},
        {"account_id": "a2", "account_code": "6601010", "account_name": "Ads",
         "l1_code": "6601", "plan_by_month": {}, "plan_year": "0"},
    ]
    nc_monthly = {"a1": {1: "80.00", 2: "90.00"}}
    partners = {"a1": [
        {"partner_id": "p1", "partner_name": "ACME", "by_month": {1: "50.00", 2: "60.00"}, "year_total": "110.00"},
        {"partner_id": None, "partner_name": None, "by_month": {1: "30.00"}, "year_total": "30.00"},
    ]}
    return accounts, nc_monthly, partners


def test_header_and_structure():
    accounts, nc_monthly, partners = _sample()
    data = build_partner_export_xlsx(accounts=accounts, nc_monthly=nc_monthly,
        partners_by_account=partners, fiscal_year=2026, cost_center_label="All Cost Centers")
    rows = _rows(data)
    hdr = next(r for r in rows if r and r[0] == "Category")
    assert hdr[:5] == ("Category", "Account Code", "Account Name", "Vendor", "Metric")
    assert hdr[5] == "Jan" and hdr[16] == "Dec" and hdr[17] == "Year"


def test_account_plan_and_nc_and_vendors():
    accounts, nc_monthly, partners = _sample()
    data = build_partner_export_xlsx(accounts=accounts, nc_monthly=nc_monthly,
        partners_by_account=partners, fiscal_year=2026, cost_center_label="All Cost Centers")
    rows = _rows(data)
    plan = _find(rows, "5101010", "Plan", "(subtotal)")
    assert plan[5] == 100.0 and plan[17] == 1200.0
    nc = _find(rows, "5101010", "NC", "(subtotal)")
    assert nc[5] == 80.0 and nc[6] == 90.0 and nc[17] == 170.0
    vendors = {r[3] for r in rows if r and r[1] == "5101010" and r[4] == "NC" and r[3] != "(subtotal)"}
    assert "ACME" in vendors and "(no vendor)" in vendors


def test_grand_total():
    accounts, nc_monthly, partners = _sample()
    data = build_partner_export_xlsx(accounts=accounts, nc_monthly=nc_monthly,
        partners_by_account=partners, fiscal_year=2026, cost_center_label="All Cost Centers")
    rows = _rows(data)
    gt_plan = next(r for r in rows if r and r[0] == "Grand Total" and r[4] == "Plan")
    gt_nc = next(r for r in rows if r and r[0] == "Grand Total" and r[4] == "NC")
    assert gt_plan[17] == 1200.0
    assert gt_nc[17] == 170.0
