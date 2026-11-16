"""The status help (app/domain/status_help.py, the user 2026-11-16): every order Status, On Hold
reason and Detail has a text; each is short enough for a popover; every button a text names is a real
label in the front end; the endpoint needs sales.read and returns the same dict."""
import re
from pathlib import Path

from app.domain.order_status import DELIVERY_DUE, HOLD_REASONS, ORDER_STATUSES, SENDS
from app.domain.status_help import DETAIL_HELP, HOLD_HELP, MAX_BODY, SHORT_SHIPPED, STATUS_HELP, payload
from tests.test_orders_api import READER, NOBODY, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401

FRONT = Path(__file__).resolve().parents[2] / "sales" / "src"


def test_every_status_reason_and_detail_has_a_text():
    assert set(STATUS_HELP) == set(ORDER_STATUSES)
    assert set(HOLD_HELP) == set(HOLD_REASONS)
    assert set(DETAIL_HELP) == {SENDS, DELIVERY_DUE, SHORT_SHIPPED}


def test_texts_are_short_and_plain():
    for key, h in {**STATUS_HELP, **HOLD_HELP, **DETAIL_HELP}.items():
        assert 20 <= len(h.body) <= MAX_BODY, (key, len(h.body))
        assert "WAITING_" not in h.body and "_940" not in h.body, key  # the label users see, never a constant


def _front_labels() -> str:
    """The screens' code (features/**, not tests): where a button's words are written."""
    return "\n".join(p.read_text(encoding="utf-8") for p in (FRONT / "features").rglob("*.tsx") if ".test." not in p.name)


def is_button(label: str, src: str) -> bool:
    """`label` is written as a button's or menu item's words: its text (>Label<), a label prop or key
    (label="Label" / label: 'Label'), a branch of a ternary that picks the words (? 'Label' : / : 'Label'}),
    or the fixed start of a template (`Back to ${to}`)."""
    q = re.escape(label)
    return bool(re.search(rf">\s*{q}\s*<|label[=:]\s*['\"]{q}['\"]|\?\s*['\"]{q}['\"]\s*:|:\s*['\"]{q}['\"]\s*\}}|`{q}\s*\$\{{", src))


def test_every_named_button_is_a_real_label():
    src = _front_labels()
    for key, h in {**STATUS_HELP, **HOLD_HELP, **DETAIL_HELP}.items():
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
    assert (await api.get("/api/v1/help/order-statuses", headers=NOBODY)).status_code == 403
    r = await api.get("/api/v1/help/order-statuses", headers=READER)
    assert r.status_code == 200 and r.json() == payload()
    assert r.json()["holds"]["997 Timeout"]["body"].startswith("No 997")
