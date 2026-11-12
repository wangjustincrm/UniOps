"""Invoice lines (FR-OB-30, A.9) and the 810 JSON."""
import json
from datetime import date
from decimal import Decimal

from app.domain.invoicing import Header810, Item810, LineIn, build_810, build_lines


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
    ship = {"name": "DC", "address1": "1 Road", "city": "Toronto", "province": "ON", "zip": "M1M 1M1", "country": "CA", "gln": "28"}
    bill = {"name": "HQ", "address1": "2 Road", "city": "Ottawa", "province": "ON", "zip": "K1K 1K1", "code": "99"}
    data = json.loads(build_810(Header810("LBL", 1000123, "PO-1", date(2026, 11, 5), date(2026, 10, 19), ship, bill), [
        Item810("CF0092", "B-1", "10062639300019", "Formula", 10, 10, Decimal("108.6"), 6),
        Item810("CF0093", "B-2", "10062639300026", "Formula 2", 0, 60, Decimal("126.6"), 6)]))
    [inv] = data["Invoice"]
    assert (inv["TransactionType"], inv["AccountingID"], inv["Invoice"], inv["InvoiceDate"], inv["ShipDate"], inv["PO"], inv["PODate"]) == (
        "810", "LBL", "1000123", "2026-11-05", "2026-11-05", "PO-1", "2026-10-19")
    assert (inv["SCAC"], inv["CarrierPro"], inv["BillofLading"], inv["TermsDescription"], inv["NetDaysDue"]) == (
        "WIFS", "CRM|1000123", "001000123", "NA", 0)
    assert (inv["ShipTo"]["ShipToCode"], inv["ShipTo"]["ShipToZipcode"], inv["ShipTo"]["ShiptoCountry"]) == ("28", "M1M 1M1", "CA")
    assert (inv["BillTo"]["BilltoCode"], inv["BillTo"]["BilltoCity"], inv["BillTo"]["BilltoCountry"]) == ("99", "Ottawa", "CA")
    assert inv["TotalCasesShipped"] == 10.0
    one, two = inv["Items"]  # a line not shipped stays on the invoice with 0
    assert (one["Line"], one["VendorPart"], one["UPC"], one["GTIN#"], one["QuantityShipped"], one["QuantityOrdered"],
            one["UOM"], one["UnitPrice"], one["PackSize"], one["ofInnerPacks"]) == (
        1, "CF0092", "10062639300019", "10062639300019", 10, 10, "CA", 108.6, 1, 6)
    assert (two["Line"], two["QuantityShipped"], two["QuantityOrdered"]) == (2, 0, 60)


def test_the_810_goes_back_to_the_mailbox_the_850_named_and_only_then():
    ship = {"name": "DC", "address1": "1 Road", "city": "Toronto", "province": "ON", "zip": "M1M 1M1", "country": "CA"}
    items = [Item810("CF0092", None, "10062639300019", "Formula", 1, 1, Decimal("10"), 6)]
    sobeys = json.loads(build_810(Header810("Sobeys", 1, "PO-1", date(2026, 11, 5), None, ship, ship, "MBX0000001"), items))
    assert sobeys["Invoice"][0]["EDIMailbox"] == "MBX0000001"
    walmart = json.loads(build_810(Header810("Walmart", 1, "PO-1", date(2026, 11, 5), None, ship, ship), items))
    assert "EDIMailbox" not in walmart["Invoice"][0]  # a partner without one gets the file as before
