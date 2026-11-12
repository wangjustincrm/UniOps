import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.scripts.complete_invoices import checks, parse


def test_the_list_takes_a_po_and_its_invoice_date_in_either_form():
    assert parse("PO #\tInvoice Date\n4550827924\t2026/11/11\n4630564306 2026-11-10\n\nnot a line") == [
        ("4550827924", date(2026, 11, 11)), ("4630564306", date(2026, 11, 10))]


def test_what_a_person_should_look_at():
    inv = SimpleNamespace(total=Decimal("10"))
    line = SimpleNamespace(line_no=1, unit_price=Decimal("10"))
    good = json.dumps({"Invoice": [{"EDIMailbox": "MBX", "ShipTo": {"ShipToCode": "1"}, "Items": [{"QuantityShipped": 1}]}]}).encode()
    assert checks(inv, [line], good, "MBX") == []
    bad = json.dumps({"Invoice": [{"ShipTo": {"ShipToCode": ""}, "Items": [{"QuantityShipped": 0}]}]}).encode()
    assert checks(inv, [SimpleNamespace(line_no=1, unit_price=None)], bad, "MBX") == [
        "line 1: no price", "the 810 does not carry the order's EDI mailbox", "the 810 ship-to has no code (GLN)", "the 810 ships nothing"]
