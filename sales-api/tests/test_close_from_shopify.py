from app.domain.status import Fulfillment as F
from app.scripts.close_from_shopify import decide, path

TRACKED = [{"tracking_numbers": ["1Z"]}]
UNTRACKED = [{"tracking_numbers": []}]


def test_the_store_decides_what_became_of_the_order():
    assert decide({"fulfillment_status": "fulfilled", "financial_status": "paid"}, TRACKED).to is F.SHIPPED
    assert decide({"fulfillment_status": "fulfilled", "financial_status": "partially_refunded"}, UNTRACKED).to is F.SHIPPED
    assert decide({"fulfillment_status": "fulfilled", "financial_status": "refunded"}, UNTRACKED).to is F.CANCELLED
    assert decide({"fulfillment_status": "fulfilled", "financial_status": "refunded"}, TRACKED).to is F.SHIPPED
    assert decide({"cancelled_at": "2026-09-01", "fulfillment_status": "fulfilled"}, TRACKED).to is F.CANCELLED
    assert decide({"closed_at": "2024-10-01"}, []).to is F.CANCELLED
    assert decide({"closed_at": "2024-10-01"}, TRACKED).to is F.SHIPPED


def test_an_order_still_open_in_the_store_is_left():
    assert decide({"financial_status": "paid"}, []).to is None


def test_the_steps_follow_the_state_machine():
    assert path(F.ACCEPTED_940, F.SHIPPED) == [F.SHIPPED]
    assert path(F.ACCEPTED_940, F.CANCELLED) == [F.ON_HOLD, F.CANCELLED]
    assert path(F.REJECTED_940, F.CANCELLED) == [F.CANCELLED]
    assert path(F.REJECTED_940, F.SHIPPED) == [F.SHIPPED]  # a late 945 may still ship it
