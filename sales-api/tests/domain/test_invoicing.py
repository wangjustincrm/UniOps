"""Invoice lines (FR-OB-30, A.9) and the 810 JSON."""
import json
from datetime import date
from decimal import Decimal

from app.domain.invoicing import Header810, LineIn, build_810, build_lines


def line(**kw):
    base = dict(order_line_id=1, line_no=1, item_code="CF0092", buyer_part="W-999", upc="062639300012", gtin=None,
                description="Formula", delivered_ea=60, case_factor=6, order_factor=Decimal(6), unit_price=Decimal("50"))
    return LineIn(**{**base, **kw})


def test_cases_at_the_850_price_and_part_cases_are_a_problem():
    b = build_lines([line()])
    [ln] = b.lines
    assert (ln.qty, ln.uom, ln.unit_price, ln.amount, b.problems) == (10, "CA", Decimal("50.00000"), Decimal("500.00"), [])
    b = build_lines([line(delivered_ea=58)])
    assert b.lines[0].qty == 9 and b.problems == [
        "CF0092: 58 EA is not a whole number of cases of 6 (9 cases + 4 EA) — invoiced as 9 only if a person confirms"]


def test_a_line_ordered_in_ea_is_priced_per_case():
    [ln] = build_lines([line(order_factor=Decimal(1), unit_price=Decimal("9"), delivered_ea=12)]).lines
    assert (ln.qty, ln.unit_price, ln.amount) == (2, Decimal("54.00000"), Decimal("108.00"))


def test_no_case_unit_or_no_price_is_a_problem_and_zero_delivered_is_left_out():
    b = build_lines([line(case_factor=None), line(order_line_id=2, line_no=2, unit_price=None),
                     line(order_line_id=3, line_no=3, delivered_ea=0)])
    assert b.lines == [] and len(b.problems) == 2


def test_the_810_names_what_a9_names():
    b = build_lines([line()])
    data = json.loads(build_810(Header810("LBL", 1000123, "PO-1", date(2026, 11, 5), "Net 30", "CAD"), b.lines))
    [inv] = data["Invoice"]
    assert (inv["AccountingID"], inv["Invoice"], inv["InvoiceDate"], inv["ShipDate"], inv["SCAC"], inv["CarrierPro"]) == (
        "LBL", "1000123", "2026-11-05", "2026-11-05", "WIFS", "CRM|1000123")
    assert inv["Taxes"] == inv["Charges"] == inv["Allowances"] == []
    assert inv["Lines"][0]["QuantityShipped"] == 10 and inv["Lines"][0]["UOM"] == "CA" and inv["Total"] == "500.00"
