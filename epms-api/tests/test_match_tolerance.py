"""3-way match tolerance rule (Phase a A2, FIN-AP-001) — pure-function tests."""
from decimal import Decimal

from app.crud.invoice import within_tolerance

D = Decimal


def test_exact_match_always_passes():
    assert within_tolerance(D("0"), D("0"), D("0")) is True
    assert within_tolerance(D("0"), D("0"), D("5")) is True


def test_zero_tolerance_blocks_any_variance():
    """Historical behaviour preserved: default tolerance 0 ⇒ any variance → exception."""
    assert within_tolerance(D("0.01"), D("0.0001"), D("0")) is False
    assert within_tolerance(D("-0.01"), D("-0.0001"), D("0")) is False


def test_variance_within_tolerance_passes():
    assert within_tolerance(D("5.00"), D("0.5000"), D("1")) is True
    assert within_tolerance(D("-5.00"), D("-0.5000"), D("1")) is True
    assert within_tolerance(D("10.00"), D("1.0000"), D("1")) is True   # boundary inclusive


def test_variance_above_tolerance_blocks():
    assert within_tolerance(D("20.00"), D("2.0000"), D("1")) is False
    assert within_tolerance(D("-20.00"), D("-2.0000"), D("1")) is False


def test_missing_pct_blocks():
    """reference_total == 0 ⇒ variance_pct meaningless — never auto-pass."""
    assert within_tolerance(D("5.00"), None, D("10")) is False
