from decimal import Decimal

import pytest

from app.platform.values import (
    ConversionError, CurrencyMismatch, FractionalQuantity, MissingConversion, Money, Quantity, UnitTable,
    line_total, total,
)

UNITS = UnitTable(base="EA", factors={"CASE": Decimal(6), "2PACK": Decimal(2), "HALF": Decimal("0.5")})


def test_quantity_converts_through_the_table():
    assert Quantity(Decimal(3), "CASE").to_ea(UNITS) == Quantity(Decimal(18), "EA")
    assert Quantity(Decimal(5), "EA").to_ea(UNITS) == Quantity(Decimal(5), "EA")
    assert Quantity(Decimal(4), "HALF").to_ea(UNITS) == Quantity(Decimal(2), "EA")


def test_missing_conversion_never_defaults_to_one():
    with pytest.raises(MissingConversion) as e:
        Quantity(Decimal(1), "PALLET").to_ea(UNITS)
    assert e.value.unit == "PALLET"
    with pytest.raises(MissingConversion):
        Quantity(1, "X").to_ea(UnitTable("EA", {"X": Decimal(0)}))


def test_fractional_ea_is_refused_like_a_missing_conversion():
    with pytest.raises(FractionalQuantity) as e:
        Quantity(Decimal(3), "HALF").to_ea(UNITS)
    assert isinstance(e.value, ConversionError) and e.value.base_amount == Decimal("1.5")
    with pytest.raises(FractionalQuantity):
        Quantity(Decimal("0.5"), "EA").to_ea(UNITS)
    # A base that is not counted in pieces may be fractional.
    kg = UnitTable("KG", {"G": Decimal("0.001")}, whole_base=False)
    assert Quantity(Decimal(250), "G").to_base(kg) == Quantity(Decimal("0.25"), "KG")


def test_money_keeps_full_precision_and_rounds_only_when_asked():
    price = Money(Decimal("1.005"), "CAD")
    assert price.amount == Decimal("1.005")
    assert price * 1000 == Money(Decimal("1005"), "CAD")
    assert Money("12.34567", "CAD").amount == Decimal("12.34567")
    assert price.rounded() == Money(Decimal("1.01"), "CAD")
    assert Money(Decimal("-1.005"), "CAD").rounded().amount == Decimal("-1.01")  # half-up, away from zero


def test_line_and_invoice_totals_round_half_up_once():
    assert line_total(Money("0.33335", "CAD"), 3) == Money(Decimal("1.00"), "CAD")  # 1.00005 → 1.00
    assert line_total(Money("1.005", "CAD"), Quantity(1, "EA")).amount == Decimal("1.01")
    lines = [line_total(Money("1.005", "CAD"), 1), line_total(Money("2.0049", "CAD"), 1)]
    assert total(lines, "CAD") == Money(Decimal("3.01"), "CAD")


def test_floats_are_refused_everywhere():
    for bad in (lambda: Money(1.1, "CAD"), lambda: Quantity(1.5, "EA"), lambda: Money("1", "CAD") * 1.5,
                lambda: 1.5 * Money("1", "CAD"), lambda: line_total(Money("1", "CAD"), 2.0),
                lambda: Quantity(1, "CASE").to_ea(UnitTable("EA", {"CASE": 6.0}))):
        with pytest.raises(TypeError):
            bad()


def test_money_refuses_mixed_currencies_and_unknown_ones():
    assert Money(Decimal("1.10"), "CAD") + Money(Decimal("2.20"), "CAD") == Money(Decimal("3.30"), "CAD")
    with pytest.raises(CurrencyMismatch):
        Money(Decimal(1), "CAD") + Money(Decimal(1), "USD")
    with pytest.raises(ValueError):
        Money(Decimal(1), "EUR")
