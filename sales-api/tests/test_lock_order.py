"""One order of locks (review of stage 3, 2026-11-24): an order before its 940, a transfer before its 943 — the outbox, the 997s,
the timeouts and a person's operations — so none of them waits for another that waits for it. Two real sessions each time."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.domain.status import Fulfillment as F
from app.models.documents import Document, DocumentLink
from app.models.orders import Order
from app.models.transfers import Transfer
from app.platform.actors import SYSTEM_ACTOR
from app.platform.commands import ANY_VERSION
from app.services.order_holds import OrderActions
from app.services.order_views import OrderService
from app.services.outbound import SEND_940
from app.services.transfer_views import TransferService
from app.services.transfers import SEND_943
from tests.sending import rows, says
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import AFTER_DELAY, a_paid_order, ack997, docs, edi, sent_order, status, store_997  # noqa: F401 — edi is a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import ack997 as ack943
from tests.test_transfers import create, p7, sent_transfer, store  # noqa: F401 — p7 is a fixture


@pytest.fixture
def t(edi):  # noqa: F811
    return SimpleNamespace(edi=edi, factory=edi.env.factory, q=edi.env.q, store=edi.store, worker=edi.worker)


async def meets(t, owner, owner_id, doc_id, coming, meanwhile):
    """A person holds the record; `coming` arrives and must wait for it holding nothing of its document; the person goes on to
    the document (`meanwhile`) and commits; `coming` then ends. With the other order of locks the two wait for each other."""
    async with t.factory(OP) as u:
        await u.session.get(owner, owner_id, with_for_update=True)
        task = asyncio.create_task(coming())
        await asyncio.sleep(0.5)
        free = (await u.session.execute(sa.select(Document.id).where(Document.id == doc_id).with_for_update(skip_locked=True))).scalar()
        if task.done() or free is None:
            await u.rollback()
            await asyncio.wait({task}, timeout=5)
            pytest.fail("it did not wait for the record, or held its document meanwhile")
        await meanwhile(u)
        await u.commit()
    return await asyncio.wait_for(task, 10)


# ── an order and its 940 ─────────────────────────────────────────────────────

async def test_the_997_and_a_replacement(t):
    o, d = await sent_order(t.edi)
    await store_997(t.edi, ack997(d["st02"]))
    await meets(t, Order, o.id, d["id"], t.edi.out.process_997s,
                lambda u: OrderService(u).send_940_now(o.id, t.edi.out, replace=True, version=ANY_VERSION, reason="wrong quantity"))
    assert await status(t.edi, o.id) == F.GENERATED_940  # the replacement stands; the old file keeps its answer
    assert [(x["file_status"], x["ack_status"]) for x in await docs(t.edi)] == [("Replaced", "Accepted"), ("Ready To Send", "Waiting")]
    assert (await t.q("SELECT processing_status FROM sales_documents WHERE kind = '997_EDI'"))[0]["processing_status"] == "Processed"


async def test_the_997_first_then_a_replacement_looks_again(t):
    o, d = await sent_order(t.edi)
    seen = (await t.q("SELECT version FROM sales_orders WHERE id = :i", i=o.id))[0]["version"]
    await store_997(t.edi, ack997(d["st02"]))
    assert (await t.edi.out.process_997s()).acks == 1 and await status(t.edi, o.id) == F.ACCEPTED_940

    async def press():
        async with t.factory(OP) as u:
            await OrderService(u).send_940_now(o.id, t.edi.out, replace=True, version=seen, reason="wrong quantity")
    assert (await says(press)).code == "entity.stale"


async def test_the_timeout_and_a_replacement(t):
    o, d = await sent_order(t.edi)
    async with t.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(Document).where(Document.id == d["id"]).values(sent_at=AFTER_DELAY - timedelta(hours=5)))
        await u.commit()
    r = await meets(t, Order, o.id, d["id"], t.edi.out.timeouts,
                    lambda u: OrderService(u).send_940_now(o.id, t.edi.out, replace=True, version=ANY_VERSION, reason="wrong quantity"))
    assert (r.timeouts, r.failed) == (0, 0) and await status(t.edi, o.id) == F.GENERATED_940  # replaced: nothing is overdue


async def test_the_timeout_alone_still_times_out(t):
    o, d = await sent_order(t.edi)
    async with t.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(Document).where(Document.id == d["id"]).values(sent_at=AFTER_DELAY - timedelta(hours=5)))
        await u.commit()
    assert (await t.edi.out.timeouts()).timeouts == 1 and await status(t.edi, o.id) == F.TIMEOUT_940


async def test_the_outbox_s_claim_and_a_cancellation(t):
    o = await a_paid_order(t.edi)
    await t.edi.out.queue_due()
    [d] = await docs(t.edi)
    out = await meets(t, Order, o.id, d["id"], t.worker._claim,
                      lambda u: OrderActions(u, t.edi.env.tasks).cancel(o.id, "the customer called", ANY_VERSION))
    assert out == ("cancelled", None) and await status(t.edi, o.id) == F.CANCELLED
    assert [r["status"] for r in await rows(t, SEND_940)] == ["CANCELLED"] and t.store.files == {}


async def test_the_outbox_s_outcome_and_a_person(t):
    """The hooks after the call take the two in the same order."""
    o = await a_paid_order(t.edi)
    await t.edi.out.queue_due()
    [d] = await docs(t.edi)
    row = SimpleNamespace(payload={"document_id": d["id"], "order_id": o.id})

    async def hook():
        async with t.factory(SYSTEM_ACTOR) as u:
            return [x.id for x in await t.edi.out._pair(row, u)]

    async def nothing(u):
        await u.session.get(Document, d["id"], with_for_update=True)
    assert await meets(t, Order, o.id, d["id"], hook, nothing) == [d["id"], o.id]


async def test_a_997_that_matches_none_or_several_is_said_as_before(t):
    o, d = await sent_order(t.edi)
    await store_997(t.edi, ack997("999999999"))
    assert (await t.edi.out.process_997s()).unmatched_997 == 1
    other, d2 = await sent_order(t.edi, number=9302)
    async with t.factory(SYSTEM_ACTOR) as u:  # two files with one number
        await u.session.execute(sa.update(Document).where(Document.id == d2["id"]).values(gs06=d["st02"], st02=d["st02"]))
        await u.commit()
    await store_997(t.edi, ack997(d["st02"]), "997-two.edi")
    assert (await t.edi.out.process_997s()).unmatched_997 == 1
    titles = [r["title"] for r in await t.q("SELECT title FROM tasks WHERE type = 'sales_997_unmatched' ORDER BY created_at")]
    assert "matches no 940s" in titles[0] and "matches several 940s" in titles[1]
    assert await status(t.edi, o.id) == F.SENT_940 == await status(t.edi, other.id)


async def unlinked(t, d):
    async with t.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.delete(DocumentLink).where(DocumentLink.document_id == d["id"]))
        await u.commit()


async def test_a_997_for_a_940_of_no_order_is_a_task_and_the_others_go_on(t):
    """Some old files have no order link (18 on dev2): a late 997 for one is looked at by a person, not an error of the pass."""
    o, d = await sent_order(t.edi)
    other, d2 = await sent_order(t.edi, number=9302)
    await unlinked(t, d)
    await store_997(t.edi, ack997(d["st02"]), "997-lost.edi")
    await store_997(t.edi, ack997(d2["st02"], "A", "A", "1"), "997-ok.edi")
    r = await t.edi.out.process_997s()
    assert (r.unmatched_997, r.acks, r.failed) == (1, 1, 0)
    [task] = await t.q("SELECT title, description FROM tasks WHERE type = 'sales_997_unmatched'")
    assert task["title"] == "997 matches a 940 of no order" and "997-lost.edi" in task["description"]
    assert await status(t.edi, other.id) == F.ACCEPTED_940
    assert [x["ack_status"] for x in await docs(t.edi)] == ["Waiting", "Accepted"]  # the lost one is left as it is


async def test_an_overdue_940_of_no_order_does_not_stop_the_others(t):
    o, d = await sent_order(t.edi)
    other, d2 = await sent_order(t.edi, number=9302)
    async with t.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(Document).where(Document.id.in_([d["id"], d2["id"]])).values(sent_at=AFTER_DELAY - timedelta(hours=5)))
        await u.commit()
    await unlinked(t, d)
    r = await t.edi.out.timeouts()
    assert (r.timeouts, r.failed) == (1, 1) and await status(t.edi, other.id) == F.TIMEOUT_940


# ── a transfer and its 943 ───────────────────────────────────────────────────

async def queued_943(p7):  # noqa: F811
    x = await create(p7)
    async with p7.factory(OP) as u:
        await TransferService(u).ready(p7.tr, x.id, ANY_VERSION)
        await u.commit()
    await p7.tr.queue_ready()
    d = (await p7.q("SELECT max(id) i FROM sales_documents WHERE kind = '943_EDI'"))[0]["i"]
    return x, d


async def state(p7, x):  # noqa: F811
    [r] = await p7.q("SELECT t.status, t.version, d.file_status FROM sales_transfers t JOIN sales_documents d ON d.id = t.document_id WHERE t.id = :i", i=x.id)
    return r["status"], r["version"], r["file_status"], [x["status"] for x in await rows(p7, SEND_943)]


async def test_the_997_and_an_amendment(p7):  # noqa: F811
    x = await sent_transfer(p7)
    [d] = await p7.q("SELECT id, st02 FROM sales_documents WHERE kind = '943_EDI'")
    await store(p7, "997_EDI", ack943(d["st02"]), "997-a.edi")
    await meets(p7, Transfer, x.id, d["id"], p7.edi.out.process_997s, lambda u: p7.tr.amend(
        u, x.id, version=ANY_VERSION, lines=[{"item_code": "CF0092", "qty_ea": 12}], notes=None, reason="wrong quantity"))
    assert (await state(p7, x))[0] == "943 Generated"  # the amendment stands
    assert [r["file_status"] for r in await p7.q("SELECT file_status FROM sales_documents WHERE kind = '943_EDI' ORDER BY id")] == ["Replaced", "Ready To Send"]


async def test_the_943_s_outcome_and_a_person(p7):  # noqa: F811
    x, d = await queued_943(p7)

    async def hook():
        async with p7.factory(SYSTEM_ACTOR) as u:
            return [y.id for y in await p7.tr._pair({"document_id": d, "transfer_id": x.id}, u)]

    async def nothing(u):
        await u.session.get(Document, d, with_for_update=True)
    assert await meets(p7, Transfer, x.id, d, hook, nothing) == [d, x.id]
