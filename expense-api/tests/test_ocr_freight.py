"""Courier invoice reading (EPMS Freight) — the pure assembly step.

The three `_*_PARSED` fixtures are what a correct read of the real sample
invoices looks like (UPS 00004E4F77416, FedEx 2-749-42565, Purolator
550316833; freight design §3.5). The model call itself is not exercised here.
"""
from decimal import Decimal

import pytest

from app.services.ocr_freight import assemble_freight_result

_UPS_PARSED = {
    "carrier": "UPS", "invoice_number": "00004E4F77416", "account_number": "4E4F77",
    "invoice_date": "2026-10-10", "due_date": "2026-10-26", "currency": "CAD",
    "amount_due": 86.82, "subtotal": 85.14, "tax_total": 1.68,
    "tax_by_category": [{"category": "On-Call Pickup Requests", "taxable_amount": 12.96, "tax": 1.68}],
    "lines": [
        {"charge_type": "shipment", "category": "Worldwide Service", "tracking_no": "1Z4E4F776832551028",
         "package_tracking_nos": ["1Z4E4F776832551028", "1Z 4E4F77 6821544637"],
         "reference": "44461957964814303-93747573", "ship_date": "2026-10-06",
         "description": "Standard — Kingston → Madison WI", "amount": 51.28, "tax": None},
        {"charge_type": "pickup", "category": "On-Call Pickup Requests", "tracking_no": None,
         "package_tracking_nos": [], "reference": "29B0D7A1695", "ship_date": "2026-10-06",
         "description": "Same Day Pickup - Web Request", "amount": 6.48, "tax": None},
        {"charge_type": "pickup", "category": "On-Call Pickup Requests", "tracking_no": None,
         "package_tracking_nos": [], "reference": "29070E50AA1", "ship_date": "2026-10-08",
         "description": "Same Day Pickup - Web Request", "amount": 6.48, "tax": None},
        {"charge_type": "fee", "category": "Fees", "tracking_no": None, "package_tracking_nos": [],
         "reference": None, "ship_date": "2026-09-12", "description": "Late Payment Fee",
         "amount": 20.90, "tax": None},
    ],
}

_PUROLATOR_PARSED = {
    "carrier": "PUROLATOR", "invoice_number": "550316833", "invoice_date": "2026-09-26",
    "due_date": "2026-10-10", "currency": "CAD", "amount_due": 133.77, "subtotal": 121.83,
    "tax_total": 11.94, "tax_by_category": [],
    "lines": [
        {"charge_type": "shipment", "tracking_no": "335929747038", "package_tracking_nos": [],
         "ship_date": "2026-09-14", "amount": 48.68, "tax": 2.43},
        {"charge_type": "shipment", "tracking_no": "335934330192", "package_tracking_nos": [],
         "ship_date": "2026-09-18", "amount": 44.20, "tax": 5.75},
        {"charge_type": "shipment", "tracking_no": "335938502838", "package_tracking_nos": [],
         "ship_date": "2026-09-24", "amount": 28.95, "tax": 3.76},
    ],
}

_FEDEX_PARSED = {
    "carrier": "FEDEX", "invoice_number": "2-749-42565", "invoice_date": "2026-09-28",
    "currency": "CAD", "amount_due": 1126.47, "subtotal": 1126.47, "tax_total": 0,
    "lines": [
        {"charge_type": "shipment", "tracking_no": "877326061702",
         "package_tracking_nos": ["877326061702", "877326061713"], "reference": "PO-099-2608-01",
         "ship_date": "2026-09-17", "amount": 1110.07, "tax": None},
        {"charge_type": "pickup", "tracking_no": None, "reference": "000054034297",
         "ship_date": "2026-09-17", "amount": 16.40, "tax": None},
    ],
}


def test_ups_category_tax_lands_on_the_pickups_only():
    r = assemble_freight_result(_UPS_PARSED)
    assert r["status"] == "ok" and r["warnings"] == []
    assert (r["amount"], r["tax_amount"]) == ("85.14", "1.68")
    ship, p1, p2, fee = r["lines"]
    assert ship["package_tracking_nos"] == ["1Z4E4F776832551028", "1Z4E4F776821544637"]
    assert (ship["tax_amount"], fee["tax_amount"]) == ("0.00", "0.00")
    assert Decimal(p1["tax_amount"]) + Decimal(p2["tax_amount"]) == Decimal("1.68")
    assert r["checks"]["unattributed_tax"] == "0.00"


def test_purolator_per_shipment_tax_is_kept():
    r = assemble_freight_result(_PUROLATOR_PARSED)
    assert r["status"] == "ok"
    assert [l["tax_amount"] for l in r["lines"]] == ["2.43", "5.75", "3.76"]
    assert all(l["tracking_no"] == l["package_tracking_nos"][0] for l in r["lines"])


def test_summary_tax_totals_listed_as_categories_are_not_counted_twice():
    # Real Haiku read of Purolator 550316833 (2026-10-19): per-shipment tax AND the
    # summary page's GST/HST totals returned as categories.
    parsed = dict(_PUROLATOR_PARSED, tax_by_category=[
        {"category": "GST", "taxable_amount": 48.68, "tax": 2.43},
        {"category": "HST ON", "taxable_amount": 73.15, "tax": 9.51}])
    r = assemble_freight_result(parsed)
    assert r["warnings"] == []
    assert [l["tax_amount"] for l in r["lines"]] == ["2.43", "5.75", "3.76"]


def test_fedex_multi_package_inbound_without_tax():
    r = assemble_freight_result(_FEDEX_PARSED)
    assert r["status"] == "ok"
    assert r["lines"][0]["package_tracking_nos"] == ["877326061702", "877326061713"]
    assert r["lines"][0]["reference"] == "PO-099-2608-01"
    assert r["lines"][1]["charge_type"] == "pickup"


def test_missing_line_is_flagged_not_hidden():
    parsed = dict(_UPS_PARSED, lines=_UPS_PARSED["lines"][:3])   # the late fee was dropped
    r = assemble_freight_result(parsed)
    assert r["status"] == "mismatch"
    assert any("64.24" in w and "85.14" in w for w in r["warnings"])


def test_outstanding_balance_mistaken_for_amount_due_is_flagged():
    # The model took "total amount outstanding" ($354.11) for this invoice's amount due.
    r = assemble_freight_result(dict(_UPS_PARSED, amount_due=354.11))
    assert r["status"] == "mismatch"
    assert not r["checks"]["header_adds_up"]


def test_category_tax_with_no_matching_line_is_reported():
    parsed = dict(_UPS_PARSED, tax_by_category=[{"category": "Brokerage", "tax": 1.68}])
    r = assemble_freight_result(parsed)
    assert r["checks"]["unattributed_tax"] == "1.68"
    assert any("Brokerage" in w for w in r["warnings"])


@pytest.mark.parametrize("bad", [None, "", "n/a"])
def test_line_without_amount_is_skipped_with_warning(bad):
    parsed = dict(_FEDEX_PARSED, lines=_FEDEX_PARSED["lines"] + [
        {"charge_type": "fee", "description": "Mystery", "amount": bad}])
    r = assemble_freight_result(parsed)
    assert len(r["lines"]) == 2
    assert any("Mystery" in w for w in r["warnings"])
