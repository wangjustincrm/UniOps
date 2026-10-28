from decimal import Decimal

import pytest

from app.platform.values import CurrencyMismatch, MissingConversion, Money, Quantity, UnitTable

UNITS = UnitTable(base="EA", factors={"CASE": Decimal(6), "2PACK": Decimal(2)})


def test_quantity_converts_through_the_table():
    assert Quantity(Decimal(3), "CASE").to_ea(UNITS) == Quantity(Decimal(18), "EA")
    assert Quantity(Decimal(5), "EA").to_ea(UNITS) == Quantity(Decimal(5), "EA")


def test_missing_conversion_never_defaults_to_one():
    with pytest.raises(MissingConversion) as e:
        Quantity(Decimal(1), "PALLET").to_ea(UNITS)
    assert e.value.unit == "PALLET"
    with pytest.raises(MissingConversion):
        Quantity(1, "X").to_ea(UnitTable("EA", {"X": Decimal(0)}))


def test_money_quantises_and_refuses_mixed_currencies():
    assert Money(Decimal("1.005"), "CAD").amount == Decimal("1.01")
    assert Money(Decimal("1.10"), "CAD") + Money(Decimal("2.20"), "CAD") == Money(Decimal("3.30"), "CAD")
    with pytest.raises(CurrencyMismatch):
        Money(Decimal(1), "CAD") + Money(Decimal(1), "USD")
    with pytest.raises(ValueError):
        Money(Decimal(1), "EUR")
