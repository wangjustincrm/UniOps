"""Quantity rendering on documents: no exponent form, no padding zeros."""
from decimal import Decimal

import pytest

from app.services.pdf_template import qty_text


@pytest.mark.parametrize(
    "stored,shown",
    [
        # The regression: normalize() raises the exponent of an integral value,
        # so every multiple of ten printed as scientific notation.
        ("30.0000", "30"),
        ("100.0000", "100"),
        ("12000.0000", "12,000"),
        # Fractions keep their digits, minus the padding.
        ("1.5000", "1.5"),
        ("2.2500", "2.25"),
        ("0.0001", "0.0001"),
        ("0.0000", "0"),
        ("-30.0000", "-30"),
    ],
)
def test_qty_text(stored, shown):
    assert qty_text(Decimal(stored)) == shown
