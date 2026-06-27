"""Unit tests for mileage (MIL) total computation.

Regression: selecting Round Trip doubled total_km but left total_amount
unchanged, because the total trusted the client-sent per-trip `amount`
while km was independently doubled from `is_round_trip`. The server must be
the single source of truth for the round-trip multiplier.
"""
from decimal import Decimal
from types import SimpleNamespace

from app.crud.expense import _compute_totals_mil


def _trip(distance, rate, round_trip, amount):
    # `amount` here simulates whatever the client sent (possibly one-way).
    return SimpleNamespace(
        distance_km=Decimal(distance),
        rate_per_km=Decimal(rate),
        is_round_trip=round_trip,
        amount=Decimal(amount),
    )


def test_round_trip_doubles_amount_and_km():
    # One-way distance 100 @ 0.72, round trip. Client sent only the one-way
    # amount (72.00) — a stale/buggy client. Server must still compute 144.
    trips = [_trip("100", "0.72", True, "72.00")]
    total, tax, net, total_km = _compute_totals_mil(trips)

    assert total_km == Decimal("200.00")
    assert total == Decimal("144.00"), "amount must double for round trip"
    assert net == Decimal("144.00")
    assert tax == Decimal("0")
    # per-trip amount is corrected in place
    assert trips[0].amount == Decimal("144.00")


def test_one_way_not_doubled():
    trips = [_trip("100", "0.72", False, "72.00")]
    total, tax, net, total_km = _compute_totals_mil(trips)
    assert total_km == Decimal("100.00")
    assert total == Decimal("72.00")


def test_multiple_trips_mixed():
    trips = [
        _trip("100", "0.72", True, "0"),   # 144
        _trip("50", "0.72", False, "0"),   # 36
    ]
    total, _, _, total_km = _compute_totals_mil(trips)
    assert total_km == Decimal("250.00")
    assert total == Decimal("180.00")
