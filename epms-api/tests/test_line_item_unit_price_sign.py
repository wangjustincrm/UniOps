"""Line items may carry a zero or negative unit price (discount / credit lines).

Requirement: on Create PO (and PR — the line-item schema and the shared frontend
validator are common to both), a line's unit_price may be 0 or negative so a
discount/rebate line can be entered. Quantity must still be strictly positive.
"""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.po import PoLineItemIn
from app.schemas.pr import PrLineItemIn


def _po(**kw):
    base = dict(description="Rebate", qty=Decimal("1"), unit="ea", unit_price=Decimal("10"))
    base.update(kw)
    return base


def _pr(**kw):
    base = dict(description="Rebate", qty=Decimal("1"), unit="ea", unit_price=Decimal("10"))
    base.update(kw)
    return base


def test_po_line_allows_negative_unit_price():
    line = PoLineItemIn(**_po(unit_price=Decimal("-5")))
    assert line.unit_price == Decimal("-5")
    assert line.line_total == Decimal("-5.00")


def test_po_line_allows_zero_unit_price():
    line = PoLineItemIn(**_po(unit_price=Decimal("0")))
    assert line.line_total == Decimal("0.00")


def test_po_line_still_rejects_nonpositive_qty():
    with pytest.raises(ValidationError):
        PoLineItemIn(**_po(qty=Decimal("0")))


def test_pr_line_allows_negative_unit_price():
    line = PrLineItemIn(**_pr(unit_price=Decimal("-5")))
    assert line.unit_price == Decimal("-5")
    assert line.line_total == Decimal("-5.00")


def test_pr_line_still_rejects_nonpositive_qty():
    with pytest.raises(ValidationError):
        PrLineItemIn(**_pr(qty=Decimal("-1")))
