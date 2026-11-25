"""The rule of a later 945 (app/domain/later_part.py): part, more, or refused and why — every condition, for both who ask."""
from dataclasses import replace

import pytest

from app.domain.later_part import JOB, MORE, PART, PRESS, Facts, ItemFigures, answer

CAN = ItemFigures("CF0093", carried=3, earlier=2, this=1)
SHIPPED = Facts(source_type="SHOPIFY", fulfillment="Shipped", held_from=None, cancelled_at_source=False, historical=False, shadow=False,
                own_940=True, invoice="Not Required", invoiced=False, child=False, items=(CAN,))
HELD = replace(SHIPPED, fulfillment="On Hold", held_from="Shipped")
BY = {JOB: SHIPPED, PRESS: HELD}


@pytest.mark.parametrize("by", [JOB, PRESS])
def test_exactly_what_was_left_is_a_part_one_above_is_more(by):
    f = BY[by]
    assert answer(f, by=by).kind == PART and answer(f, by=by).refused is None
    assert answer(replace(f, items=(replace(CAN, this=1, earlier=0),)), by=by).kind == PART  # still short: a part too
    a = answer(replace(f, items=(replace(CAN, this=2), ItemFigures("CF0092", 6, 0, 6))), by=by)
    assert a.kind == MORE and [(i.item_code, i.above) for i in a.over] == [("CF0093", 1)]
    # Never by the job: a corrected 945 cannot be told from an extra one. A person is refused nothing.
    assert a.refused == ("it is more than the 940 carried: CF0093: 1 EA above the 3 on the 940 (4 with this 945)" if by == JOB else None)


@pytest.mark.parametrize("by", [JOB, PRESS])
@pytest.mark.parametrize("items", [(CAN,), (replace(CAN, this=9),)], ids=["part", "more"])
@pytest.mark.parametrize("change,says", [
    (dict(source_type="EDI_850"), "only a Shopify order"), (dict(source_type="MANUAL"), "only a Shopify order"),
    (dict(shadow=True), "old one"), (dict(historical=True), "old one"), (dict(own_940=False), "sent no 940"),
    (dict(cancelled_at_source=True), "cancelled at source"), (dict(child=True), "made from this one"),
    (dict(invoiced=True), "invoice"), (dict(invoice="Blocked"), "Blocked"), (dict(invoice="Ready to Send"), "Ready to Send"),
    (dict(invoice="Sent"), "Sent"), (dict(returned=("CF0093",)), "refund or a return is recorded for CF0093"),
    (dict(adjusted=("CF0093",)), "taken out twice"), (dict(unmatched=("CF0010 1 EA",)), "no line of the 940: CF0010 1 EA"),
    (dict(fulfillment="Cancelled"), "Cancelled"),
])
def test_what_refuses_a_part_refuses_more_too(by, items, change, says):
    if by == PRESS and change == {"fulfillment": "Cancelled"}:
        says = "not On Hold"
    a = answer(replace(BY[by], items=items, **change), by=by)
    assert a.kind is None and says in a.refused


def test_the_job_wants_shipped_a_person_wants_held_since_shipped():
    assert "not Shipped" in answer(HELD, by=JOB).refused
    assert "not On Hold" in answer(SHIPPED, by=PRESS).refused
    assert "not On Hold" in answer(replace(HELD, held_from="940 Sent"), by=PRESS).refused
    two = answer(replace(HELD, waiting=("b.edi",)), by=PRESS).refused
    assert "b.edi" in two and "Keep the applied 945" in two and "ask the warehouse" in two and "adjustment" in two  # what to do
    assert answer(replace(SHIPPED, waiting=("b.edi",)), by=JOB).kind == PART  # beside a kept one
    assert answer(replace(HELD, items=()), by=PRESS).kind is None


@pytest.mark.parametrize("invoice", ["Not Required", "Pending", "Awaiting Requested Date", "Waiting for Delivery Confirmation"])
def test_an_invoice_not_built_yet_is_no_refusal(invoice):
    assert answer(replace(SHIPPED, invoice=invoice), by=JOB).kind == PART
