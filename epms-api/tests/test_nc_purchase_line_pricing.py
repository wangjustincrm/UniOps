"""The mirrored unit price must belong to the mirrored quantity.

The mirror stores a PO line's quantity in NC's QUOTATION unit
(``nastnum`` / ``castunitid`` — LB, LTR, PIECES, whatever the buyer ordered in).
``norigtaxprice`` is the price per NC's MAIN unit, and NC keeps a separate
``nqtorigtaxprice`` for the quotation unit. Pairing the main-unit price with the
quotation-unit quantity produces a line whose own arithmetic does not hold.

Production, live today: PO-078-2411-01 is 3,900 LB at CAD 2.50/LB = 9,750, and
the mirror shows 3,900 LB at 5.5556 — a 2.22x overstatement, because 1 kg is
2.2 lb and the price it copied was per kg. PO-019-2505-01 has the same shape at
26.95 vs 12.25.

Checked against NC before writing: ``nastnum * nqtorigtaxprice`` reproduces
``norigtaxmny`` on all 629 in-scope order lines — no nulls, no exceptions. The
fallback to ``norigtaxprice`` exists for lines the ERP leaves without a
quotation price, where the two units are the same thing anyway.
"""
import uuid
from decimal import Decimal

from app.services.nc_purchase_sync.transform import transform

VEND = {"0000415": (uuid.uuid4(), "Lactalis Canada")}


def _raw(*, nastnum, nnum, price_main, price_quote, line_total, unit="LB"):
    """One order, one line, priced the way NC prices a converted unit."""
    return {
        "orders": [{"pk_order": "O1", "vbillcode": "PO-078-2411-01",
                    "dbilldate": "2026-05-01 00:00:00", "pk_supplier": "S1",
                    "corigcurrencyid": "C1", "ntotalorigmny": line_total,
                    "forderstatus": 3, "modifiedtime": "2026-05-01 09:00:00",
                    "vmemo": None, "vtrantypecode": "21-Cxx-CRM01"}],
        "order_lines": [{"pk_order_b": "OL1", "pk_order": "O1", "crowno": "10",
                         "pk_material": "M1", "vvendinventoryname": "Widget",
                         "castunitid": "U1", "nastnum": nastnum, "nnum": nnum,
                         "norigtaxprice": price_main, "nqtorigtaxprice": price_quote,
                         "ntaxrate": Decimal("0"), "ctaxcodeid": "T1",
                         "norigtaxmny": line_total, "norigmny": line_total,
                         "ntax": Decimal("0"), "dplanarrvdate": None}],
        "arrivals": [], "arrival_lines": [],
        "suppliers": {"S1": "0000415"}, "materials": {"M1": ("CR0256", "Widget")},
        "uoms": {"U1": unit}, "currencies": {"C1": "CAD"},
        "max_modifiedtime": "2026-05-03 09:00:00",
    }


def test_price_is_per_the_unit_the_quantity_is_in():
    """PO-078-2411-01, to the digit: 3,900 LB, CAD 9,750."""
    raw = _raw(nastnum=Decimal("3900"), nnum=Decimal("1755"),
               price_main=Decimal("5.55555556"), price_quote=Decimal("2.5"),
               line_total=Decimal("9750"))

    ln = transform(raw, VEND)["order_lines"][0]

    assert ln["qty"] == Decimal("3900") and ln["unit"] == "LB"
    assert ln["unit_price"] == Decimal("2.5")
    assert ln["qty"] * ln["unit_price"] == ln["line_total"], (
        f"{ln['qty']} x {ln['unit_price']} != {ln['line_total']}")


def test_a_one_to_one_unit_is_unchanged():
    """Most lines quote and stock in the same unit, and both NC prices agree.
    The fix must not move any of those."""
    raw = _raw(nastnum=Decimal("38000"), nnum=Decimal("38000"),
               price_main=Decimal("1.25"), price_quote=Decimal("1.25"),
               line_total=Decimal("47500"), unit="KGM")

    ln = transform(raw, VEND)["order_lines"][0]

    assert ln["unit_price"] == Decimal("1.25")
    assert ln["qty"] * ln["unit_price"] == ln["line_total"]


def test_a_missing_quotation_price_falls_back_to_the_main_one():
    """NULL means the ERP never quoted separately — which only happens when the
    two units are the same. Falling back beats writing 0 into a price column."""
    raw = _raw(nastnum=Decimal("100"), nnum=Decimal("100"),
               price_main=Decimal("3.5"), price_quote=None,
               line_total=Decimal("350"), unit="EA")

    ln = transform(raw, VEND)["order_lines"][0]

    assert ln["unit_price"] == Decimal("3.5")


def test_a_zero_quotation_price_falls_back_too():
    """A real zero-price line still reconciles: line_total is 0 as well. But a
    zero standing in for 'not set' would silently zero out a priced line, so the
    fallback is on falsiness, not on None alone."""
    raw = _raw(nastnum=Decimal("100"), nnum=Decimal("100"),
               price_main=Decimal("3.5"), price_quote=Decimal("0"),
               line_total=Decimal("350"), unit="EA")

    ln = transform(raw, VEND)["order_lines"][0]

    assert ln["unit_price"] == Decimal("3.5")
