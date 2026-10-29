"""Value objects (design §13.2)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


class ConversionError(Exception):
    """A quantity cannot be turned into the warehouse's base unit exactly. The
    caller sends the order to review (FR-OB-15) instead of guessing."""


class MissingConversion(ConversionError):
    """The item has no factor for this unit. Never treated as 1: the caller
    sends the order to review instead (the warehouse ships exactly the base
    quantity it is told)."""

    def __init__(self, unit: str) -> None:
        super().__init__(f"no conversion factor for unit {unit!r}")
        self.unit = unit


class FractionalQuantity(ConversionError):
    """The conversion yields a fraction of a base unit (e.g. 1 CASE of 6 →
    0.5 of a 12-pack's base). Never rounded: the warehouse ships whole EA
    (product owner, 2026-10-28)."""

    def __init__(self, amount: Decimal, unit: str, base_amount: Decimal, base: str) -> None:
        super().__init__(f"{amount} {unit} is {base_amount} {base}, not a whole number")
        self.unit = unit
        self.base_amount = base_amount


def _decimal(value: object, what: str) -> Decimal:
    """Exact decimals only: a float has already lost the value (0.1 is
    0.1000000000000000055…), so it is refused rather than converted."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{what} must be a Decimal, int or str, not {type(value).__name__}")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        return Decimal(value)
    raise TypeError(f"{what} must be a Decimal, int or str, not {type(value).__name__}")


@dataclass(frozen=True)
class UnitTable:
    """One item's conversions: `factors[unit]` base units per `unit`.
    The base unit itself needs no entry. `whole_base`: the base unit is
    counted in whole pieces (EA), so a fractional result is refused."""

    base: str
    factors: Mapping[str, Decimal]
    whole_base: bool = True

    def factor(self, unit: str) -> Decimal:
        if unit == self.base:
            return Decimal(1)
        f = self.factors.get(unit)
        if f is None or f <= 0:
            raise MissingConversion(unit)
        return _decimal(f, "conversion factor")


@dataclass(frozen=True)
class Quantity:
    amount: Decimal
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _decimal(self.amount, "Quantity amount"))

    def to_base(self, conversions: UnitTable) -> Quantity:
        """Raises MissingConversion when the table has no factor for `unit`,
        FractionalQuantity when the result is not a whole base unit (both are
        ConversionError)."""
        base_amount = self.amount * conversions.factor(self.unit)
        if conversions.whole_base and base_amount != base_amount.to_integral_value():
            raise FractionalQuantity(self.amount, self.unit, base_amount, conversions.base)
        return Quantity(base_amount, conversions.base)

    # The warehouse's base unit is EA; the name the design uses.
    to_ea = to_base


# ISO 4217 minor units for the currencies in use; anything else is refused
# rather than guessed.
_MINOR_UNITS = {"CAD": 2, "USD": 2}


class CurrencyMismatch(Exception):
    pass


@dataclass(frozen=True)
class Money:
    """An amount at full precision (unit prices carry 5+ decimals, as in NC).
    Nothing rounds implicitly: `rounded()` and `line_total()` round half-up to
    the currency's minor unit, and are what line and invoice totals use
    (product owner, 2026-10-28)."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.currency not in _MINOR_UNITS:
            raise ValueError(f"unsupported currency {self.currency!r}")
        object.__setattr__(self, "amount", _decimal(self.amount, "Money amount"))

    @property
    def minor_unit(self) -> Decimal:
        return Decimal(1).scaleb(-_MINOR_UNITS[self.currency])

    def rounded(self) -> Money:
        return Money(self.amount.quantize(self.minor_unit, rounding=ROUND_HALF_UP), self.currency)

    def _same(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(self.amount * _decimal(factor, "Money factor"), self.currency)

    __rmul__ = __mul__


def line_total(unit_price: Money, quantity: Decimal | int | Quantity) -> Money:
    """unit price × quantity, rounded half-up to the minor unit once."""
    qty = quantity.amount if isinstance(quantity, Quantity) else _decimal(quantity, "quantity")
    return (unit_price * qty).rounded()


def total(lines: list[Money], currency: str) -> Money:
    """Sum of already-rounded line totals (an invoice total), rounded."""
    out = Money(Decimal(0), currency)
    for m in lines:
        out = out + m
    return out.rounded()
