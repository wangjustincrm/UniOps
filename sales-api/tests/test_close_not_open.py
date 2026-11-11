import pytest

from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.scripts.close_not_open import fulfillment_steps, invoice_steps


def test_an_unshipped_order_is_cancelled_along_the_state_machine():
    assert fulfillment_steps(F.ACCEPTED_940) == [F.ON_HOLD, F.CANCELLED]
    assert fulfillment_steps(F.REJECTED_940) == [F.CANCELLED]
    assert fulfillment_steps(F.TIMEOUT_940) == [F.CANCELLED]


def test_a_shipped_order_invoiced_outside_sales_goes_to_sent_through_ready():
    assert invoice_steps(Inv.WAITING_FOR_DELIVERY_CONFIRMATION, None) == [Inv.READY, Inv.SENT]
    assert invoice_steps(Inv.AWAITING_REQUESTED_DATE, None) == [Inv.WAITING_FOR_DELIVERY_CONFIRMATION, Inv.READY, Inv.SENT]
    assert invoice_steps(Inv.BLOCKED, Inv.WAITING_FOR_DELIVERY_CONFIRMATION) == [
        Inv.WAITING_FOR_DELIVERY_CONFIRMATION, Inv.READY, Inv.SENT]


def test_a_blocked_invoice_without_its_held_status_is_left():
    with pytest.raises(ValueError):
        invoice_steps(Inv.BLOCKED, None)
