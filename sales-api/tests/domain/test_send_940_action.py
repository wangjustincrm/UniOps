"""Send 940 is offered only while the order can still go to the warehouse."""
import pytest

from app.domain.order_rules import send_940_action


@pytest.mark.parametrize(("fulfillment", "kw", "want"), [
    ("Pending", {}, "send"),
    ("940 Timeout", {}, "send"),
    ("940 Accepted", {}, "replace"),
    ("Shipped", {}, None),
    ("Pending", {"has_945": True}, None),            # a 945 came: the goods left
    ("Pending", {"source_type": "FROM_945"}, None),  # made from a 945 (4550827924 on dev2)
    ("Pending", {"historical": True}, None),
    ("Cancelled", {}, None),
])
def test_send_940_follows_the_order(fulfillment, kw, want):
    args = {"source_type": "EDI_850", "historical": False, "has_945": False} | kw
    assert send_940_action(fulfillment, **args) == want
