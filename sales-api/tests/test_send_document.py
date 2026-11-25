"""Send again from the file register (stage 3 k): the file's owner's own permission and rules — one rule each, in the service
— once, and for an invoice already sent the same why as from the invoice."""
import asyncio
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.domain.status import Fulfillment as F
from app.models.config import Connection
from app.models.documents import Document, DocumentLink
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.platform.authorization import PermissionDenied
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.errors import BusinessError, InvalidInput, NotFound
from app.services.document_actions import DOCUMENT_ACTIONS
from app.services.invoice_views import InvoiceService
from app.services.invoicing import SEND_INVOICE
from app.services.outbound import SEND_940
from app.services.settings import SETTINGS, SHADOW_MODE
from app.services.transfers import SEND_943
from tests import acting
from tests import test_send_940 as s940
from tests import test_send_943 as s943
from tests import test_send_invoice as sinv
from tests.sending import STATE_IDS, STATES, put_row, rows, said, says, twice, way_out
from tests.test_document_actions import _stored
from tests.test_invoicing import OP, inv_status, p6  # noqa: F401 — a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_outbound import a_paid_order, docs, status
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import p7  # noqa: F401 — a fixture

WHY = "lost in the mail"


def runtime(t):
    edi = getattr(t, "edi", t)
    return SimpleNamespace(outbound=edi.out, transfers=getattr(t, "tr", None), invoicing=getattr(t, "inv", None))


def press(t, did, reason=None):
    async def go():
        async with t.factory(OP) as u:
            await acting.send_again(u, did, runtime(t), reason)
            await u.commit()
    return go


async def act(t, did, reason=None):
    async with t.factory(OP) as u:
        out = await run_action(u, DOCUMENT_ACTIONS, "send_again", ActionIn(ids={did: None}, reason=reason), runtime(t))
        await u.commit()
    return out.results[0]


async def its_file(t, kind) -> int:
    return (await t.q("SELECT max(id) i FROM sales_documents WHERE kind = :k", k=kind))[0]["i"]


async def can_write(t, on):
    async with t.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, t.edi.conn_id)).can_write = on
        await u.commit()


@pytest.fixture
def t(p4_edi):  # noqa: F811
    return SimpleNamespace(edi=p4_edi, factory=p4_edi.env.factory, q=p4_edi.env.q, store=p4_edi.store, worker=p4_edi.worker)


async def refused_940(t):
    o = await a_paid_order(t.edi)
    await can_write(t, False)
    await t.edi.out.queue_due()
    await t.worker.run_once()
    await can_write(t, True)
    assert await status(t.edi, o.id) == F.FAILED_940
    return o, await its_file(t, "940_EDI")


# ── in each state of what is under way ───────────────────────────────────────

@pytest.mark.parametrize(("state", "minutes", "expected"), STATES, ids=STATE_IDS)
async def test_a_940_s_file_in_each_state(t, state, minutes, expected):
    o, did = await refused_940(t)
    await put_row(t, SEND_940, {"document_id": did, "order_id": o.id}, ("sales_orders", o.id), state, minutes)
    e = await says(press(t, did))
    assert said(e) == expected and (not expected or expected[1] != "unknown" or way_out(e))
    assert len(await docs(t.edi)) == (1 if expected else 2)


@pytest.mark.parametrize(("state", "minutes", "expected"), STATES, ids=STATE_IDS)
async def test_a_943_s_file_in_each_state(p7, state, minutes, expected):  # noqa: F811
    x = await s943.failed(p7)
    did = await its_file(p7, "943_EDI")
    await put_row(p7, SEND_943, {"document_id": did, "transfer_id": x.id}, ("sales_transfers", x.id), state, minutes)
    e = await says(press(p7, did))
    assert said(e) == expected and (not expected or expected[1] != "unknown" or way_out(e))
    assert len(await s943.n_943(p7)) == (1 if expected else 2)


@pytest.mark.parametrize(("state", "minutes", "expected"), STATES, ids=STATE_IDS)
async def test_an_invoice_s_file_in_each_state(p6, state, minutes, expected):  # noqa: F811
    _, inv = await sinv.sent(p6)
    did = await its_file(p6, "810_JSON")
    await put_row(p6, SEND_INVOICE, {"invoice_id": inv.id}, ("sales_invoices", inv.id), state, minutes)
    e = await says(press(p6, did, WHY))
    assert said(e) == expected and (not expected or expected[1] != "unknown" or way_out(e))
    assert len(await rows(p6, SEND_INVOICE)) == 2 + (0 if expected else 1)


# ── a file that fails its checks is kept as Failed and queues nothing: a problem, never "queued" ──

async def test_a_940_that_fails_its_checks_from_its_file_is_a_problem(t):
    o, did = await refused_940(t)
    await s940.set_order(t, o.id, ship_to={"name": "A B", "address1": "1 rue", "city": "Montreal", "province": "QC", "country": "CA"})  # no postal code
    before = await rows(t, SEND_940)
    r = await act(t, did)
    assert r.status == "done" and r.note is None and r.problem.startswith("the 940 failed its checks and was not sent: ")
    assert await status(t.edi, o.id) == F.FAILED_940 and await rows(t, SEND_940) == before


async def test_a_943_that_fails_its_checks_from_its_file_is_a_problem(p7):  # noqa: F811
    await s943.failed(p7)
    did = await its_file(p7, "943_EDI")
    await s943.no_weight(p7)
    before = await rows(p7, SEND_943)
    r = await act(p7, did)
    assert r.status == "done" and r.note is None and r.problem.startswith("the 943 failed its checks and was not sent: ")
    assert await rows(p7, SEND_943) == before


async def test_one_that_is_queued_says_its_number(t):
    _, did = await refused_940(t)
    r = await act(t, did)
    assert (r.status, r.problem) == ("done", None) and r.note == f"940 {(await docs(t.edi))[-1]['st02']} queued"


# ── two at once, from here and from its own page ─────────────────────────────

async def test_two_presses_on_a_940_s_file_make_one(t):
    _, did = await refused_940(t)
    e = await twice(t, OP, lambda u: acting.send_again(u, did, runtime(t)))
    assert said(e) == ("send.queued", "pending")
    await t.worker.run_once()
    assert len(s940.puts(t)) == 1


async def test_two_presses_on_a_943_s_file_make_one(p7):  # noqa: F811
    await s943.failed(p7)
    did = await its_file(p7, "943_EDI")
    e = await twice(p7, OP, lambda u: acting.send_again(u, did, runtime(p7)))
    assert said(e) == ("send.queued", "pending")
    await p7.worker.run_once()
    assert len(s943.puts(p7)) == 1


async def test_an_invoice_from_its_file_and_from_its_page_at_once_go_once(p6):  # noqa: F811
    _, inv = await sinv.sent(p6)
    did, v, before = await its_file(p6, "810_JSON"), await sinv.version(p6, inv.id), len(sinv.puts(p6))
    calls = iter([lambda u: acting.send_again(u, did, runtime(p6), WHY),
                  lambda u: acting.send_invoice(u, p6.inv, inv.id, v, again=True, reason=WHY)])
    e = await twice(p6, OP, lambda u: next(calls)(u))
    assert said(e) == ("send.queued", "pending")
    await p6.worker.run_once()
    assert len(sinv.puts(p6)) == before + 1


# ── an invoice: the same confirm and reason as from the invoice ──────────────

async def test_an_invoice_already_sent_says_why_and_it_is_in_its_activity(p6):  # noqa: F811
    _, inv = await sinv.sent(p6)
    did = await its_file(p6, "810_JSON")
    for reason in (None, "", " ok "):
        e = await says(press(p6, did, reason))
        assert (e.code, e.status_code, e.details) == ("invoice.resend_reason", 422, {"field": "reason"})
        with pytest.raises(BusinessError) as x:
            await act(p6, did, reason)
        assert (x.value.code, x.value.status_code, x.value.details) == ("invoice.resend_reason", 422, {"field": "reason"})
    assert len(await rows(p6, SEND_INVOICE)) == 1
    r = await act(p6, did, WHY)
    assert r.status == "done" and r.note == f"invoice {inv.invoice_number} queued"
    async with p6.factory(OP) as u:
        timeline = (await InvoiceService(u).detail(inv.id)).timeline
    assert [(e.user_name, e.reason) for e in timeline if e.action == "send_again"] == [(OP.name, f"invoice sent again: {WHY}")]
    r = await act(p6, did, WHY)
    assert (r.status, r.code) == ("failed", "send.queued")


async def test_one_that_never_went_asks_nothing(p6):  # noqa: F811
    o, inv = await sinv.ready(p6)
    p6.store.fail["put"] = RuntimeError("connection reset")
    assert await says(sinv.press(p6, inv.id, await sinv.version(p6, inv.id))) is None
    await p6.worker.run_once()
    del p6.store.fail["put"]
    assert await inv_status(p6, o.id) == "Failed"
    assert await says(press(p6, await its_file(p6, "810_JSON"))) is None
    assert (await p6.worker.run_once()).done == 1 and await inv_status(p6, o.id) == "Sent"


# ── the owner's rules, permission and gates ──────────────────────────────────

async def test_a_live_940_is_replaced_only_from_its_order(t):
    o = await a_paid_order(t.edi)
    await t.edi.out.queue_due()
    await t.worker.run_once()
    did = await its_file(t, "940_EDI")
    assert (await says(press(t, did, WHY))).code == "send940.live"
    assert (await act(t, did, WHY)).code == "send940.live" and await status(t.edi, o.id) == F.SENT_940
    assert len(await docs(t.edi)) == 1


async def test_a_940_s_file_linked_to_no_order_is_said_on_both_ways(t):
    """Some old files have no order link: a refusal, never an error of the server's."""
    _, did = await refused_940(t)
    async with t.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.delete(DocumentLink).where(DocumentLink.document_id == did))
        await u.commit()
    e = await says(press(t, did, WHY))
    assert e.code == "document.no_order" and "linked to no order" in e.message
    r = await act(t, did, WHY)
    assert (r.status, r.reason) == ("skipped", e.message)
    assert len(await docs(t.edi)) == 1 and len(await rows(t, SEND_940)) == 1  # nothing made, nothing queued


async def test_a_943_s_file_of_no_transfer_is_said_on_both_ways(p7):  # noqa: F811
    await s943.failed(p7)
    did = await its_file(p7, "943_EDI")
    async with p7.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Document, did)).body_json = {"errors": []}
        await u.commit()
    e = await says(press(p7, did))
    assert e.code == "document.no_transfer" and "linked to no transfer" in e.message
    r = await act(p7, did)
    assert (r.status, r.reason) == ("skipped", e.message) and len(await s943.n_943(p7)) == 1


async def test_a_943_that_went_is_not_sent_again(p7):  # noqa: F811
    await s943.sent_transfer(p7)
    did = await its_file(p7, "943_EDI")
    assert (await says(press(p7, did))).code == "transfer.not_failed" and (await act(p7, did)).code == "transfer.not_failed"


async def test_the_shadow_run_and_a_cancelled_order_stop_it(p6):  # noqa: F811
    o, inv = await sinv.sent(p6)
    did = await its_file(p6, "810_JSON")

    async def shadow(on):
        async with p6.factory(SYSTEM_ACTOR) as u:
            await SETTINGS.set(u.session, SHADOW_MODE, on)
            await u.commit()
    await shadow(True)
    assert (await says(press(p6, did, WHY))).code == "invoice.shadow" and (await act(p6, did, WHY)).code == "invoice.shadow"
    await shadow(False)
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, o.id)).fulfillment_status = F.CANCELLED.value
        await u.commit()
    assert (await says(press(p6, did, WHY))).code == "invoice.order_cancelled"
    assert len(await rows(p6, SEND_INVOICE)) == 1


async def test_a_940_needs_the_register_s_permission_and_the_order_s(t, monkeypatch):
    await refused_940(t)
    await needs_both(t, monkeypatch, "940_EDI", "sales.order.operate")


async def test_a_943_needs_the_register_s_permission_and_the_transfer_s(p7, monkeypatch):  # noqa: F811
    await s943.failed(p7)
    await needs_both(p7, monkeypatch, "943_EDI", "sales.transfer.manage")


async def test_an_invoice_needs_the_register_s_permission_and_the_invoice_s(p6, monkeypatch):  # noqa: F811
    await sinv.sent(p6)
    await needs_both(p6, monkeypatch, "810_JSON", "sales.invoice.manage")


async def needs_both(t, monkeypatch, kind, owner):
    did, was = await its_file(t, kind), t.factory.permissions
    before = await rows(t, SEND_940, SEND_943, SEND_INVOICE)

    def only(*keys):
        async def check(session, actor, key):  # noqa: ARG001
            return key in keys
        return check
    for keys in ((), ("sales.read", owner), ("sales.read", "sales.file.retry"),
                 ("sales.file.retry", *({"sales.order.operate", "sales.transfer.manage", "sales.invoice.manage"} - {owner}))):
        monkeypatch.setattr(t.factory, "permissions", only(*keys))
        assert isinstance(await says(press(t, did, WHY)), PermissionDenied), keys
        with pytest.raises(PermissionDenied):  # 403 for the request, before anything: not a "failed" record
            await act(t, did, WHY)
    assert await rows(t, SEND_940, SEND_943, SEND_INVOICE) == before
    monkeypatch.setattr(t.factory, "permissions", only("sales.file.retry", owner))  # and no more is needed
    assert (await act(t, did, WHY)).status == "done"
    monkeypatch.setattr(t.factory, "permissions", was)


async def test_what_is_not_ours_to_send_is_said(p6):  # noqa: F811
    received = await _stored(p6.edi.env, b"{}", "850_JSON", "Failed")
    assert (await says(press(p6, received))).code == "document.not_outbound"
    r = await act(p6, received)
    assert r.status == "skipped" and "only a file we send" in r.reason
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Document, received)).direction = "OUT"
        await u.commit()
    assert (await says(press(p6, received))).code == "document.not_resendable"
    assert (await act(p6, received)).status == "skipped"
    assert isinstance(await says(press(p6, 99_999)), NotFound)
    r = await act(p6, 99_999)
    assert (r.status, r.code) == ("failed", "entity.not_found")
    async with p6.factory(OP) as u:
        with pytest.raises(InvalidInput) as e:
            await run_action(u, DOCUMENT_ACTIONS, "send_again", ActionIn(ids={1: None, 2: None}, reason=WHY), runtime(p6))
        assert e.value.code == "action.single"


# ── no lock of the document: its owner is what is locked ─────────────────────

@pytest.mark.parametrize("route", [press, act], ids=["route", "action"])
async def test_the_outbox_and_a_press_do_not_wait_for_each_other(p6, route):  # noqa: F811
    """The outbox holds the order and its invoice, then wants the document (its outcome): a press must not hold it meanwhile."""
    o, inv = await sinv.sent(p6)
    did = await its_file(p6, "810_JSON")
    async with p6.factory(SYSTEM_ACTOR) as worker:
        await worker.session.get(Order, o.id, with_for_update=True)
        task = asyncio.create_task(press(p6, did, WHY)() if route is press else act(p6, did, WHY))
        await asyncio.sleep(0.4)
        done = task.done()
        got = await worker.session.get(Document, did, with_for_update={"skip_locked": True})
        await worker.rollback()
        await asyncio.wait({task}, timeout=5)
        assert not done and got is not None, "the press did not wait for the order, or held the document"
        task.result()
