"""Replace 940, an order's and a transfer's amendment when the new file fails its checks (E, user 2026-11-25): nothing is replaced. The
old file, the record and its version, its lines, its tasks and the outbox stay as they were; the person is told, and the same form,
corrected, is saved with the version it has; the attempt is kept in the log and the audit, with who and why."""
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.domain.status import Fulfillment as F
from app.models.config import Item
from app.models.documents import Document
from app.models.orders import Order
from app.models.transfers import Transfer
from app.platform.actors import SYSTEM_ACTOR
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.commands import ANY_VERSION
from app.schemas.orders import AmendOrderIn
from app.schemas.transfers import AmendIn
from app.services import generated
from app.services.generated import FileRefused
from app.services.order_actions import ORDER_ACTIONS
from app.services.order_views import OrderService
from app.services.problems import CHANGED_AFTER_940, EDI_940_TIMEOUT, EDI_943_TIMEOUT, TRANSFER_FAILED, problem
from app.services.transfer_views import TransferService
from tests.conftest import make_token
from tests.test_order_amend import SHIP_TO, _order
from tests.test_invoicing import OP, p6  # noqa: F401 — p6 is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import p7, sent_transfer  # noqa: F401 — p7 is a fixture

TABLES = ("sales_orders", "sales_order_lines", "sales_transfers", "sales_transfer_lines", "sales_documents", "sales_document_links", "sales_outbox",
          "sales_shipments", "sales_notifications", "tasks")


async def whole(q):
    """Every row of what a press could change (a file's bytes aside), as it is."""
    out = {}
    for t in TABLES:
        rows = await q(f"SELECT * FROM {t} ORDER BY 1")
        out[t] = [{k: v for k, v in r.items() if k != "content"} for r in rows]
    return out


async def kept(q):
    return (await q("SELECT action, entity, entity_id, before, after, user_name, reason FROM sales_audit WHERE action LIKE '%refused' ORDER BY id"),
            await q("SELECT level, step, message, order_id, transfer_id, context FROM sales_log WHERE message LIKE '%refused%' ORDER BY id"))


async def only_the_attempt(q, before, act, entity, id, *, why, wrong):
    assert await whole(q) == before
    [a], [g] = await kept(q)
    assert (a["action"], a["entity"], a["entity_id"], a["before"], a["after"], a["user_name"]) == (act, entity, str(id), None, None, OP.name)
    assert why in a["reason"] and wrong in a["reason"]
    assert g["level"] == "WARNING" and why in g["message"] and wrong in g["message"] and "nothing changed" in g["message"]
    assert any(wrong in p for p in g["context"]["problems"]) and g["context"]["why"] == why and g["context"]["lines"]
    assert (g["order_id"] if entity == "sales_orders" else g["transfer_id"]) == id
    return g


def told(e, what, wrong):
    assert (e.code, e.status_code) == ("file.failed_checks", 422) and wrong in e.message and any(wrong in p for p in e.details["problems"])
    assert f"the new {what} failed its checks, so nothing was changed and the {what} that went stays" in e.message


# ── an order ─────────────────────────────────────────────────────────────────

async def sent(t, number="PO-E1", accepted=False):
    """A retailer order whose 940 went, its CHANGED_AFTER_940 and a timeout task open; then its ship-to loses its city."""
    o = await _order(t, number, "940 Failed")
    async with t.factory(SYSTEM_ACTOR) as u:
        row = await u.session.get(Order, o.id, with_for_update=True)
        row.ship_to, row.bill_to, row.requested_delivery_date = SHIP_TO, SHIP_TO, row.requested_delivery_date or row.order_date
        await u.commit()
    async with t.factory(OP) as u:
        await OrderService(u)._run_send_940(o.id, t.edi.out, replace=False, version=ANY_VERSION, reason=None)
        await u.commit()
    await t.edi.worker.run_once()
    async with t.factory(SYSTEM_ACTOR) as u:
        row = await u.session.get(Order, o.id, with_for_update=True)
        assert row.fulfillment_status == F.SENT_940
        if accepted:
            row.fulfillment_status = F.ACCEPTED_940.value
        for e in (CHANGED_AFTER_940, EDI_940_TIMEOUT):
            await t.edi.out.tasks.raise_problem(u.session, e, o.id, problem(e, f"the old {e}", "as it was"))
        await u.commit()
    await city(t, o.id, None)
    return o


async def city(t, oid, value):
    async with t.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, oid, with_for_update=True)).ship_to = {**SHIP_TO, "city": value}
        await u.commit()


async def seen(q, table, id):
    return (await q(f"SELECT version FROM {table} WHERE id = :i", i=id))[0]["version"]


async def line_of(q, oid):
    return (await q("SELECT id, item_code, ordered_qty FROM sales_order_lines WHERE order_id = :o", o=oid))[0]


def amend_body(ln, n, why="12 cases, not 10", **more):
    return AmendOrderIn(reason=why, lines=[{"id": ln["id"], "item_code": ln["item_code"], "ordered_qty": str(n)}], **more)


WRONG = "city is empty"


@pytest.mark.parametrize("accepted", [False, True], ids=["sent", "accepted"])
async def test_replace_keeps_the_live_940(p6, accepted):  # noqa: F811
    o = await sent(p6, accepted=accepted)
    before, v = await whole(p6.q), await seen(p6.q, "sales_orders", o.id)

    async def press(version):
        async with p6.factory(OP) as u:
            out = await run_action(u, ORDER_ACTIONS, "replace_940", ActionIn(ids={o.id: version}, reason="the quantity was wrong"), SimpleNamespace(outbound=p6.edi.out))
            await u.commit()  # as the action's route does
            return out.results[0]
    r = await press(v)
    assert (r.status, r.code) == ("failed", "file.failed_checks") and WRONG in r.reason and "the 940 that went stays" in r.reason
    await only_the_attempt(p6.q, before, "replace_refused", "sales_orders", o.id, why="the quantity was wrong", wrong=WRONG)
    await city(p6, o.id, "Ajax")  # corrected: the same press goes
    r = await press(await seen(p6.q, "sales_orders", o.id))
    assert r.status == "done" and r.note
    assert [d["file_status"] for d in await p6.q("SELECT file_status FROM sales_documents WHERE kind = '940_EDI' ORDER BY id")] == ["Replaced", "Ready To Send"]
    assert await p6.q("SELECT 1 FROM tasks WHERE type = 'sales_changed_after_940' AND NOT is_completed") == []


async def test_a_replacement_is_checked_before_anything_is_marked(p6):  # noqa: F811
    o = await sent(p6)
    before = await whole(p6.q)
    async with p6.factory(OP) as u:
        with pytest.raises(FileRefused) as e:
            await p6.edi.out.send_now(u, o.id, replace=True, version=ANY_VERSION, reason="the quantity was wrong")
        told(e.value, "940", WRONG)
        await u.commit()  # no savepoint here: there is nothing to undo
    assert await whole(p6.q) == before


@pytest.mark.parametrize("accepted", [False, True], ids=["sent", "accepted"])
async def test_an_amendment_keeps_the_live_940_and_every_line(p6, accepted):  # noqa: F811
    o = await sent(p6, accepted=accepted)
    ln, before, v = await line_of(p6.q, o.id), await whole(p6.q), await seen(p6.q, "sales_orders", o.id)
    async with p6.factory(OP) as u:
        with pytest.raises(FileRefused) as e:
            await OrderService(u).amend(o.id, amend_body(ln, 12), p6.edi.out, v)
        told(e.value, "940", WRONG)
        await u.commit()  # whoever caught it and went on keeps nothing of it
    g = await only_the_attempt(p6.q, before, "amend_refused", "sales_orders", o.id, why="12 cases, not 10", wrong=WRONG)
    assert g["context"]["asked"]["lines"][0]["ordered_qty"] in ("12", 12)


async def test_an_amendment_over_http_corrected_in_the_same_form_is_saved(p6):  # noqa: F811
    o = await sent(p6)
    ln, v = await line_of(p6.q, o.id), await seen(p6.q, "sales_orders", o.id)
    async with http(p6, {"sales.read", "sales.order.operate"}) as c:
        before = await whole(p6.q)
        body = amend_body(ln, 12).model_dump(mode="json")
        for _ in range(2):  # pressed again as it is: refused again, never as stale
            r = await c.post(f"/api/v1/orders/{o.id}/amend", params={"version": v}, json=body)
            assert (r.status_code, r.json()["error"]["code"]) == (422, "file.failed_checks") and WRONG in r.json()["error"]["message"]
        assert await whole(p6.q) == before and len((await kept(p6.q))[0]) == 2
        await city(p6, o.id, "Ajax")
        async with p6.factory(SYSTEM_ACTOR) as u:  # (the correction is the ship-to's here: the form's version is read again)
            v = (await u.session.get(Order, o.id)).version
        r = await c.post(f"/api/v1/orders/{o.id}/amend", params={"version": v}, json=body)
        assert r.status_code == 200 and r.json()["sent"] is True, r.text
    assert str((await line_of(p6.q, o.id))["ordered_qty"]).startswith("12")


async def test_an_amendment_corrected_in_its_own_lines_goes_with_the_version_it_had(p6):  # noqa: F811
    o = await sent(p6)
    await city(p6, o.id, "Ajax")
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.execute(sa.select(Item).where(Item.item_code == "CF0010"))).scalar_one().warehouse_held = False
        await u.commit()
    ln, v = await line_of(p6.q, o.id), await seen(p6.q, "sales_orders", o.id)
    async with http(p6, {"sales.read", "sales.order.operate"}) as c:
        send = lambda code, n: c.post(f"/api/v1/orders/{o.id}/amend", params={"version": v}, json={"reason": "another item", "lines": [  # noqa: E731
            {"id": ln["id"], "item_code": code, "ordered_qty": n}]})
        r = await send("CF0010", "2")  # an item the warehouse does not hold: the only line, so the 940 has none
        assert (r.status_code, r.json()["error"]["code"]) == (422, "file.failed_checks"), r.text
        assert await seen(p6.q, "sales_orders", o.id) == v
        r = await send("CF0092", "3")  # the same form, corrected, with the version it has
        assert r.status_code == 200 and r.json()["sent"] is True, r.text


@pytest.mark.parametrize("state", ["940 Rejected", "940 Timeout", "940 Failed"])
async def test_with_no_live_file_an_amendment_is_as_before(p6, state):  # noqa: F811
    o = await sent(p6)
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, o.id, with_for_update=True)).fulfillment_status = state
        d = (await u.session.execute(sa.select(Document).where(Document.kind == "940_EDI").with_for_update())).scalar_one()
        d.ack_status = {"940 Rejected": "Rejected", "940 Timeout": "Timeout"}.get(state, d.ack_status)
        if state == "940 Failed":
            d.file_status = "Failed"
        await u.commit()
    was = (await p6.q("SELECT file_status, ack_status FROM sales_documents WHERE kind = '940_EDI' ORDER BY id"))[0]
    ln = await line_of(p6.q, o.id)
    async with p6.factory(OP) as u:
        out = await OrderService(u).amend(o.id, amend_body(ln, 12), p6.edi.out, ANY_VERSION)
        await u.commit()
    assert out["sent"] is False and "failed its checks and was not sent" in out["problem"] and out["order"]["fulfillment_status"] == "940 Failed"
    assert str((await line_of(p6.q, o.id))["ordered_qty"]).startswith("12")  # the lines are the new ones; nothing live says otherwise
    docs = await p6.q("SELECT file_status, ack_status FROM sales_documents WHERE kind = '940_EDI' ORDER BY id")
    assert docs[0] == was and docs[1]["file_status"] == "Cancelled"  # the old file has its own status, not Replaced
    assert (await kept(p6.q)) == ([], [])


async def test_the_attempt_s_record_failing_never_changes_what_the_person_is_told(p6, monkeypatch, caplog):  # noqa: F811
    o = await sent(p6)
    ln, before = await line_of(p6.q, o.id), await whole(p6.q)

    async def broken(*a, **k):
        raise RuntimeError("the log is down")
    monkeypatch.setattr(generated.oplog, "write", broken)
    async with p6.factory(OP) as u:
        with pytest.raises(FileRefused):
            await OrderService(u).amend(o.id, amend_body(ln, 12), p6.edi.out, ANY_VERSION)
        await u.commit()
    assert await whole(p6.q) == before and await kept(p6.q) == ([], [])
    assert any("could not keep the refused attempt" in r.getMessage() for r in caplog.records)


async def test_the_record_s_unit_never_waits_for_the_request_s_own_lock(p6, monkeypatch, caplog):  # noqa: F811
    """A replacement holds the order's lock while its attempt is kept: were that ever to write the order, it would not hang."""
    import time
    o = await sent(p6)
    before, v = await whole(p6.q), await seen(p6.q, "sales_orders", o.id)
    monkeypatch.setattr(generated, "audit_act", lambda s, act, record: setattr(record, "notes", "written by mistake"))
    t0 = time.monotonic()
    async with p6.factory(OP) as u:
        out = await run_action(u, ORDER_ACTIONS, "replace_940", ActionIn(ids={o.id: v}, reason="the quantity was wrong"), SimpleNamespace(outbound=p6.edi.out))
        await u.commit()
    assert out.results[0].code == "file.failed_checks" and time.monotonic() - t0 < 8
    assert await whole(p6.q) == before
    assert any("could not keep the refused attempt" in r.getMessage() for r in caplog.records)


# ── a transfer ───────────────────────────────────────────────────────────────

async def weight(t, code, w):
    async with t.factory(SYSTEM_ACTOR) as u:
        (await u.session.execute(sa.select(Item).where(Item.item_code == code))).scalar_one().gross_weight_lb_per_ea = w
        await u.commit()


NO_WEIGHT = "CF0093 has no gross weight"


@pytest.mark.parametrize("state", ["Waiting for 997", "Waiting for 944"])
@pytest.mark.parametrize("ack", ["Waiting", "Timeout"])
async def test_a_transfer_s_amendment_keeps_the_943_that_went(p7, state, ack):  # noqa: F811
    x = await sent_transfer(p7)
    async with p7.factory(SYSTEM_ACTOR) as u:
        t = await u.session.get(Transfer, x.id, with_for_update=True)
        t.status = state
        (await u.session.get(Document, t.document_id)).ack_status = ack
        await p7.tr.tasks.raise_problem(u.session, EDI_943_TIMEOUT, x.id, problem(EDI_943_TIMEOUT, "the old timeout", "as it was"))
        await u.commit()
    await weight(p7, "CF0093", None)
    before, v = await whole(p7.q), await seen(p7.q, "sales_transfers", x.id)
    async with p7.factory(OP) as u:
        with pytest.raises(FileRefused) as e:
            await TransferService(u).amend(p7.tr, x.id, AmendIn(lines=[{"item_code": "CF0093", "qty_ea": 12}], notes="changed", reason="only CF0093"), v)
        told(e.value, "943", NO_WEIGHT)
        await u.commit()
    await only_the_attempt(p7.q, before, "amend_refused", "sales_transfers", x.id, why="only CF0093", wrong=NO_WEIGHT)


async def test_a_transfer_s_amendment_over_http_corrected_in_the_same_form_is_saved(p7):  # noqa: F811
    x = await sent_transfer(p7)
    await weight(p7, "CF0093", None)
    before, v = await whole(p7.q), await seen(p7.q, "sales_transfers", x.id)
    async with http(p7, {"sales.read", "sales.transfer.manage"}) as c:
        send = lambda code: c.post(f"/api/v1/transfers/{x.id}/amend", params={"version": v}, json={"reason": "wrong item", "lines": [  # noqa: E731
            {"item_code": code, "qty_ea": 600}]})
        r = await send("CF0093")
        assert (r.status_code, r.json()["error"]["code"]) == (422, "file.failed_checks") and NO_WEIGHT in r.json()["error"]["message"]
        assert await whole(p7.q) == before
        r = await send("CF0092")
        assert r.status_code == 200 and r.json()["problem"] is None and r.json()["status"] == "943 Generated", r.text
    assert [d["file_status"] for d in await p7.q("SELECT file_status FROM sales_documents WHERE kind = '943_EDI' ORDER BY id")] == ["Replaced", "Ready To Send"]


async def test_a_rejected_943_is_not_marked_replaced_by_one_that_never_went(p7):  # noqa: F811
    x = await sent_transfer(p7)
    async with p7.factory(SYSTEM_ACTOR) as u:
        t = await u.session.get(Transfer, x.id, with_for_update=True)
        t.status = "Failed"
        (await u.session.get(Document, t.document_id)).ack_status = "Rejected"
        await u.commit()
    await weight(p7, "CF0093", None)
    amend = lambda code: AmendIn(lines=[{"item_code": code, "qty_ea": 600}], reason="again")  # noqa: E731
    async with p7.factory(OP) as u:
        out = await TransferService(u).amend(p7.tr, x.id, amend("CF0093"), ANY_VERSION)
        await u.commit()
    assert out.status == "Failed" and "failed its checks" in out.problem and [(ln.item_code, ln.qty_ea) for ln in out.lines] == [("CF0093", 600)]  # as T1
    assert await p7.q(f"SELECT 1 FROM tasks WHERE type = 'sales_{TRANSFER_FAILED}' AND NOT is_completed")
    docs = await p7.q("SELECT file_status, ack_status, replaced_by_id FROM sales_documents WHERE kind = '943_EDI' ORDER BY id")
    assert (docs[0]["file_status"], docs[0]["ack_status"], docs[0]["replaced_by_id"]) == ("Sent", "Rejected", None) and docs[1]["file_status"] == "Cancelled"
    async with p7.factory(OP) as u:
        out = await TransferService(u).amend(p7.tr, x.id, amend("CF0092"), ANY_VERSION)
        await u.commit()
    assert out.status == "943 Generated" and out.problem is None


def test_the_audit_list_can_be_filtered_by_every_act_that_is_kept():
    import re
    from pathlib import Path

    import app as package
    from app.services.audit_logs import EntityChangeRepository
    acts = set()
    for p in (Path(package.__path__[0]) / "services").rglob("*.py"):
        s = p.read_text()
        acts |= set(re.findall(r'audit_act\(\w+(?:\.\w+)*, "(\w+)"', s)) | set(re.findall(r'"(\w+_refused)"', s))
    assert {"send_again", "fulfil_in_shopify", "replace_refused", "amend_refused"} == acts
    assert acts <= set(EntityChangeRepository.enums["action"])


def http(t, grants):
    """The real app on this test's database."""
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from app.core.config import settings
    from app.core.deps import get_uow_factory
    from app.main import app
    from app.runtime import build_runtime, get_runtime

    @asynccontextmanager
    async def run():
        async def matrix(session, actor, key):  # noqa: ARG001
            return key in grants
        was, t.factory.permissions = t.factory.permissions, matrix
        rt = build_runtime(settings, t.factory)
        rt = SimpleNamespace(**{k: getattr(rt, k) for k in dir(rt) if not k.startswith("_")})
        rt.outbound = getattr(getattr(t, "edi", None), "out", None) or rt.outbound
        rt.transfers = getattr(t, "tr", None) or rt.transfers
        app.dependency_overrides[get_uow_factory] = lambda: t.factory
        app.dependency_overrides[get_runtime] = lambda: rt
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers={"Authorization": f"Bearer {make_token('anyone')}"}) as c:
                yield c
        finally:
            app.dependency_overrides.clear()
            t.factory.permissions = was
    return run()
