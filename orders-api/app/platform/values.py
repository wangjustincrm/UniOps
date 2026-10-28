"""Value objects (design §13.2)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


class MissingConversion(Exception):
    """The item has no factor for this unit. Never treated as 1: the caller
    sends the order to review instead (the warehouse ships exactly the base
    quantity it is told)."""

    def __init__(self, unit: str) -> None:
        super().__init__(f"no conversion factor for unit {unit!r}")
        self.unit = unit


@dataclass(frozen=True)
class UnitTable:
    """One item's conversions: `factors[unit]` base units per `unit`.
    The base unit itself needs no entry."""

    base: str
    factors: Mapping[str, Decimal]

    def factor(self, unit: str) -> Decimal:
        if unit == self.base:
            return Decimal(1)
        f = self.factors.get(unit)
        if f is None or f <= 0:
            raise MissingConversion(unit)
        return Decimal(f)


@dataclass(frozen=True)
class Quantity:
    amount: Decimal
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))

    def to_base(self, conversions: UnitTable) -> Quantity:
        """Raises MissingConversion when the table has no factor for `unit`."""
        return Quantity(self.amount * conversions.factor(self.unit), conversions.base)

    # The warehouse's base unit is EA; the name the design uses.
    to_ea = to_base


# ISO 4217 minor units for the currencies in use; anything else is refused
# rather than guessed.
_MINOR_UNITS = {"CAD": 2, "USD": 2}


class CurrencyMismatch(Exception):
    pass


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.currency not in _MINOR_UNITS:
            raise ValueError(f"unsupported currency {self.currency!r}")
        q = Decimal(1).scaleb(-_MINOR_UNITS[self.currency])
        amount = self.amount if isinstance(self.amount, Decimal) else Decimal(str(self.amount))
        object.__setattr__(self, "amount", amount.quantize(q, rounding=ROUND_HALF_UP))

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
        return Money(self.amount * Decimal(str(factor)), self.currency)
