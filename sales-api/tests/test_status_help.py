"""The status help (app/domain/status_help.py, the user 2026-11-16): every order Status, On Hold
reason and Detail has a text; each is short enough for a popover; every button a text names is a real
label in the front end; the endpoint needs sales.read and returns the same dict."""
import re
from pathlib import Path

from app.domain.order_status import DELIVERY_DUE, HOLD_REASONS, ORDER_STATUSES, SENDS
from app.domain.order_payment import PAYMENT_STATUSES
from app.domain.status_help import (
    DETAIL_HELP, HOLD_HELP, MAX_BODY, OVER_SHIPPED, PAYMENT_HELP, SHIPMENTS, SHORT_SHIPPED, STATUS_HELP, payload)
from tests.test_orders_api import READER, NOBODY, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401

FRONT = Path(__file__).resolve().parents[2] / "sales" / "src"


def test_every_status_reason_and_detail_has_a_text():
    assert set(STATUS_HELP) == set(ORDER_STATUSES)
    assert set(HOLD_HELP) == set(HOLD_REASONS)
    assert set(DETAIL_HELP) == {SENDS, DELIVERY_DUE, SHORT_SHIPPED, OVER_SHIPPED, SHIPMENTS}
    assert set(PAYMENT_HELP) == set(PAYMENT_STATUSES)


def test_texts_are_short_and_plain():
    for key, h in {**STATUS_HELP, **HOLD_HELP, **DETAIL_HELP, **PAYMENT_HELP}.items():
        assert 20 <= len(h.body) <= MAX_BODY, (key, len(h.body))
        assert "WAITING_" not in h.body and "_940" not in h.body, key  # the label users see, never a constant


def _front_labels() -> str:
    """The screens' code (features/**, not tests): where a button's words are written (also a record action's label)."""
    return "\n".join(p.read_text(encoding="utf-8") for ext in ("*.tsx", "*.ts") for p in (FRONT / "features").rglob(ext)
                     if ".test." not in p.name)  # .ts too: a record action's words live in its definition (orderActions.ts)


def is_button(label: str, src: str) -> bool:
    """`label` is written as a button's or menu item's words: its text (>Label<), a label prop or key
    (label="Label" / label: 'Label'), a branch of a ternary that picks the words (? 'Label' : / : 'Label'}),
    or the fixed start of a template (`Back to ${to}`)."""
    q = re.escape(label)
    return bool(re.search(rf">\s*{q}\s*<|label[=:]\s*['\"]{q}['\"]|\?\s*['\"]{q}['\"]\s*:|:\s*['\"]{q}['\"]\s*\}}|`{q}\s*\$\{{", src))


def test_every_named_button_is_a_real_label():
    src = _front_labels()
    for key, h in {**STATUS_HELP, **HOLD_HELP, **DETAIL_HELP, **PAYMENT_HELP}.items():
        for b in h.buttons:
            assert b in h.body, (key, b)  # declared because the text names it
            assert is_button(b, src), f"{key}: the button {b!r} is not a button's words in sales/src/features"


def test_the_button_check_is_strict():
    src = _front_labels()
    assert not is_button("Send it to the moon", src)
    assert not is_button("Shipped", src)       # a status value compared in code, not a button
    assert not is_button("go back", src)       # the words the Cancelled at Source text once used
    assert is_button("Back to", src) and is_button("Release hold", src) and is_button("Send", src)


async def test_the_endpoint_needs_sales_read_and_serves_the_texts(api):  # noqa: F811
    assert (await api.get("/api/v1/help/texts", headers=NOBODY)).status_code == 403
    r = await api.get("/api/v1/help/texts", headers=READER)
    assert r.status_code == 200 and r.json() == payload()
    assert r.json()["holds"]["997 Timeout"]["body"].startswith("No 997")


# ── the next step (approved table, 2026-11-16) ───────────────────────────────────────────────────
from datetime import date, datetime, timedelta, timezone  # noqa: E402

from app.domain.order_status import (  # noqa: E402
    COMPLETED, DRAFT, ON_HOLD, WAITING_945, WAITING_997, WAITING_DELIVERY, WAITING_INVOICE, WAITING_TO_SEND, Display, DetailFacts,
    StatusFacts,
)
from app.domain.status import Fulfillment as F, Invoice as Inv  # noqa: E402
from app.domain.status_help import NOBODY as NOBODY_TURN  # noqa: E402 — not tests' NOBODY (a user with no grants)
from app.domain.status_help import HOLD_STEPS, SHOPIFY, STEPS, WAREHOUSE, YOU, next_step, next_step_key  # noqa: E402

TODAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def step(status, fulfillment=F.PENDING.value, reason=None, invoice=Inv.PENDING.value, delay_over=True, runs_on=True, shadow=False,
         **detail):
    d = Display(status, reason, False)
    f = StatusFacts(fulfillment=fulfillment, invoice=invoice, delay_over=delay_over, shadow=shadow)
    return next_step_key(d, f, DetailFacts(**detail), TODAY, runs_on=runs_on)


WAITING = Inv.WAITING_FOR_DELIVERY_CONFIRMATION.value


def test_every_hold_reason_has_a_step_and_every_step_text_is_short_with_real_buttons():
    assert set(HOLD_STEPS) == set(HOLD_REASONS)
    src = _front_labels()
    for key, st in {**STEPS, **HOLD_STEPS}.items():
        assert st.who in (YOU, WAREHOUSE, SHOPIFY, NOBODY_TURN) and 10 <= len(st.text) <= 120, key
        for b in st.buttons:
            assert b in st.text and is_button(b, src), (key, b)


def test_the_step_follows_the_orders_own_facts():
    assert step(DRAFT, F.DRAFT.value) == "draft"
    assert step(WAITING_TO_SEND, delay_over=False, send_at=NOW + timedelta(hours=1)) == "send.delay"
    assert step(WAITING_TO_SEND, manual_sending=True, send_at=NOW) == "send.manual"
    assert step(WAITING_TO_SEND, delay_over=True, send_at=NOW - timedelta(hours=1)) == "send.next_run"
    assert step(WAITING_997, F.SENT_940.value) == "sent"
    assert step(WAITING_945, F.ACCEPTED_940.value) == "fulfilling"
    assert step(WAITING_945, F.SHIPPED.value) == "fulfilling.tracking"
    # Shipped: only "waiting for delivery confirmation" takes Confirm delivery
    assert step(WAITING_DELIVERY, F.SHIPPED.value, invoice=WAITING, delivery_due=TODAY) == "shipped"
    assert step(WAITING_DELIVERY, F.SHIPPED.value, invoice=WAITING) == "shipped"
    assert step(WAITING_DELIVERY, F.SHIPPED.value, invoice=WAITING, delivery_due=TODAY - timedelta(days=1)) == "shipped.late"
    assert step(WAITING_DELIVERY, F.SHIPPED.value, invoice=Inv.AWAITING_REQUESTED_DATE.value,
                delivery_due=TODAY + timedelta(days=2)) == "shipped.awaiting"
    assert step(WAITING_DELIVERY, F.SHIPPED.value, invoice=Inv.PENDING.value) == "shipped.pending"
    # automatic runs off: nothing goes "by itself" or "on the next run"
    assert step(WAITING_TO_SEND, delay_over=False, send_at=NOW + timedelta(hours=1), runs_on=False) == "send.runs_off"
    assert step(WAITING_TO_SEND, runs_on=False) == "send.runs_off"
    assert step(WAITING_TO_SEND, manual_sending=True, runs_on=False) == "send.manual"
    assert step(WAITING_DELIVERY, F.SHIPPED.value, invoice=Inv.PENDING.value, runs_on=False) == "shipped.runs_off"
    assert step(WAITING_DELIVERY, F.SHIPPED.value, invoice=WAITING, runs_on=False) == "shipped"
    assert step(WAITING_INVOICE, F.SHIPPED.value, invoice=Inv.QUEUED.value, runs_on=False) == "invoice.runs_off"
    # a made 940 (GENERATED) can't be sent by hand: with runs off no step names Send 940 now
    assert step(WAITING_TO_SEND, F.GENERATED_940.value, runs_on=False) == "send.made_runs_off"  # names no button
    assert step(WAITING_TO_SEND, F.GENERATED_940.value) == "send.next_run"
    # the test run: Zoho sends it, so no step
    assert step(WAITING_TO_SEND, shadow=True) is None and step(WAITING_945, F.ACCEPTED_940.value, shadow=True) is None
    assert step(WAITING_INVOICE, F.SHIPPED.value, invoice=Inv.READY.value) == "invoice.ready"
    assert step(WAITING_INVOICE, F.SHIPPED.value, invoice=Inv.QUEUED.value) == "invoice.queued"
    assert step(COMPLETED, F.SHIPPED.value) is None
    for reason in HOLD_REASONS:
        assert step(ON_HOLD, F.ON_HOLD.value, reason=reason) == reason


def test_every_step_is_reachable():
    seen = set()
    for status in (DRAFT, WAITING_TO_SEND, WAITING_997, WAITING_945, WAITING_DELIVERY, WAITING_INVOICE, COMPLETED):
        for fulfillment in (F.DRAFT, F.PENDING, F.GENERATED_940, F.SENT_940, F.ACCEPTED_940, F.SHIPPED):
            for invoice in Inv:
                for runs_on in (True, False):
                    for delay_over in (True, False):
                        for detail in ({}, {"manual_sending": True}, {"send_at": NOW},
                                       {"delivery_due": TODAY - timedelta(days=1)}, {"delivery_due": TODAY + timedelta(days=1)}):
                            seen.add(step(status, fulfillment.value, invoice=invoice.value, runs_on=runs_on, delay_over=delay_over, **detail))
    assert set(STEPS) <= seen, set(STEPS) - seen
    st = next_step(Display(ON_HOLD, "997 Timeout", False), StatusFacts(fulfillment=F.TIMEOUT_940.value), DetailFacts(), TODAY)
    assert (st.who, st.text) == (YOU, "Check with the warehouse, then Send 940 now, or Cancel order if it should not go.")


async def test_the_order_carries_its_next_step(api):  # noqa: F811
    from tests.test_orders import shopify_order
    api.env.shop.orders = [shopify_order(1, 9301)]
    await api.env.pull()
    o = (await api.get("/api/v1/orders", headers=READER)).json()["items"][0]
    assert o["order_status"] == "To Fulfill"
    # the test server runs no automatic runs (SALES_SCHEDULER_ENABLED off, the switch paused by default): the step says so
    assert (o["next_step_who"], o["next_step"]) == (YOU, STEPS["send.runs_off"].text)


async def test_a_post_answers_with_the_same_next_step_as_a_get(api):  # noqa: F811
    from tests.test_orders_api import OPERATOR
    body = {"partner_code": "MAN", "customer_reference": "phone-1", "carrier": "Purolator", "reason": "Phone order",
            "ship_to": {"name": "A B", "phone": "514-555-0100", "address1": "1 rue", "city": "Montréal", "province": "QC", "zip": "h2x1y4"},
            "lines": [{"item_code": "CF0092", "quantity": 2, "unit": "case"}]}
    posted = (await api.post("/api/v1/orders/manual", json=body, headers=OPERATOR)).json()
    got = (await api.get(f"/api/v1/orders/{posted['id']}", headers=READER)).json()
    assert posted["next_step"] and posted["next_step"] == got["next_step"]


def test_a_read_that_never_learned_whether_runs_happen_shows_no_step():
    from app.services.order_views import OrderService
    assert OrderService._runs_on is None  # the class default: a path that forgets shows nothing, never a false promise


# ── the glossary (round 2) ───────────────────────────────────────────────────────────────────────
from app.domain.glossary import TERMS  # noqa: E402


def test_every_term_a_screen_names_is_in_the_glossary_and_short():
    src = _front_labels()
    used = set(re.findall(r"['\"]term:([^'\"`$]+)['\"]", src))  # a quoted id ('term:On hand'), not the type `term:${string}`
    assert {"Order ref", "On hand", "SSCC", "Sales, Refunds, Net"} <= used, used  # the scan really finds them
    assert used <= set(TERMS), used - set(TERMS)
    for key, h in TERMS.items():
        assert 20 <= len(h.body) <= MAX_BODY, key
        for b in h.buttons:
            assert b in h.body and is_button(b, src), (key, b)



def test_the_delay_starts_at_receipt_for_an_invoiced_partner_and_at_payment_for_a_prepaid_one():
    from app.domain.order_status import delay_start
    rec, paid = NOW - timedelta(days=1), NOW
    assert delay_start(from_receipt=True, received_at=rec, paid_at=paid) == rec
    assert delay_start(from_receipt=False, received_at=rec, paid_at=paid) == paid
    assert delay_start(from_receipt=False, received_at=rec, paid_at=None) is None  # not paid yet


async def test_the_order_says_when_its_delay_starts_and_sql_agrees(api):  # noqa: F811
    """Prepaid: at payment; invoiced: at receipt; no delay: none. The SQL (delay_over's) gives the same start."""
    from sqlalchemy import select, update

    from app.models.config import Partner
    from app.models.orders import Order
    from app.platform.actors import SYSTEM_ACTOR
    from app.services.order_status_sql import DELAY_START_SQL
    from tests.test_orders import shopify_order
    api.env.shop.orders = [shopify_order(1, 9301)]
    await api.env.pull()

    async def read():
        o = (await api.get("/api/v1/orders", headers=READER)).json()["items"][0]
        async with api.env.factory(SYSTEM_ACTOR) as u:
            sql = (await u.session.execute(select(DELAY_START_SQL).select_from(Order).where(Order.id == o["id"]))).scalar()
        return o, sql

    async def partner(**values):
        async with api.env.factory(SYSTEM_ACTOR) as u:
            await u.session.execute(update(Partner).where(Partner.code == "SHOP").values(**values))
            await u.commit()

    await partner(settlement_mode="PREPAID", delay_minutes=30)
    o, sql = await read()
    assert o["settlement_mode"] == "PREPAID" and o["delay_minutes"] == 30
    assert o["delay_starts_at"] is not None and datetime.fromisoformat(o["delay_starts_at"]) == sql
    assert o["delay_starts_at"] == o["paid_at"]
    await partner(settlement_mode="INVOICE")
    o, sql = await read()
    assert o["delay_starts_at"] == o["first_received_at"] and datetime.fromisoformat(o["delay_starts_at"]) == sql
    await partner(delay_minutes=0)
    o, _ = await read()
    assert o["delay_starts_at"] is None and o["delay_minutes"] == 0  # no delay: no start to show
