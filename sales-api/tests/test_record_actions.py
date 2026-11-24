"""The record actions of transfers, documents and counts (stage 3 c–e): the rule and the work of their single routes, one at
a time, with the reason they asked for. None sends anything itself."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.transfers import Transfer
from app.platform.actors import SYSTEM_ACTOR
from app.platform.authorization import PermissionDenied
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.errors import BusinessError, InvalidInput
from app.services.count_actions import COUNT_ACTIONS
from app.services.document_actions import DOCUMENT_ACTIONS
from app.services.transfer_actions import TRANSFER_ACTIONS
from tests.test_document_actions import _stored
from tests.test_intake import PO_CONTROL_CHARS
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_stock_counts import _lots, _received, _upload
from tests.test_transfers import create, p7, sent_transfer  # noqa: F401 — p7 is a fixture


async def go(factory, registry, id, rid, version, rt=None, **kw):
    async with factory(OP) as u:
        out = await run_action(u, registry, id, ActionIn(ids={rid: version}, **kw), rt)
        await u.commit()
    return out.results[0]


async def transfer(p7, x):  # noqa: F811
    return (await p7.q("SELECT status, version FROM sales_transfers WHERE id = :i", i=x.id))[0]


async def test_a_transfer_ready_and_cancel(p7):  # noqa: F811
    rt, x = SimpleNamespace(transfers=p7.tr), await create(p7)
    v = (await transfer(p7, x))["version"]
    r = await go(p7.factory, TRANSFER_ACTIONS, "ready", x.id, v + 7, rt)
    assert (r.status, r.code) == ("failed", "entity.stale")
    assert (await go(p7.factory, TRANSFER_ACTIONS, "ready", x.id, v, rt)).status == "done"
    assert (await transfer(p7, x))["status"] == "Ready To Send" and await p7.q("SELECT 1 FROM sales_outbox") == []  # the pass queues it
    r = await go(p7.factory, TRANSFER_ACTIONS, "ready", x.id, v, rt)
    assert r.status == "skipped" and "Ready To Send" in r.reason  # already so: said as that, whatever the version
    v = (await transfer(p7, x))["version"]
    with pytest.raises(BusinessError) as e:
        await go(p7.factory, TRANSFER_ACTIONS, "cancel", x.id, v, rt)
    assert e.value.code == "reason.required"
    assert (await go(p7.factory, TRANSFER_ACTIONS, "cancel", x.id, v, rt, reason="not needed")).status == "done"
    assert (await transfer(p7, x))["status"] == "Cancelled"


async def test_a_sent_943_is_not_cancelled_and_a_hold_is_released_with_why(p7):  # noqa: F811
    rt, x = SimpleNamespace(transfers=p7.tr), await sent_transfer(p7)
    t = await transfer(p7, x)
    r = await go(p7.factory, TRANSFER_ACTIONS, "cancel", x.id, t["version"], rt, reason="too late")
    assert r.status == "skipped" and "the 943 is sent" in r.reason
    r = await go(p7.factory, TRANSFER_ACTIONS, "release", x.id, t["version"], rt, reason="not held")
    assert r.status == "skipped" and "not On Hold" in r.reason
    async with p7.factory(SYSTEM_ACTOR) as u:
        row = await u.session.get(Transfer, x.id)
        row.status, row.status_before_hold = "On Hold", "Discrepancy"
        await u.commit()
    t = await transfer(p7, x)
    assert (await go(p7.factory, TRANSFER_ACTIONS, "release", x.id, t["version"], rt, reason="compared")).status == "done"
    assert (await transfer(p7, x))["status"] == "Discrepancy"


async def test_a_document_read_again_or_ignored_and_not_twice(env):  # noqa: F811
    rt = SimpleNamespace(tasks=env.tasks)
    did = await _stored(env, PO_CONTROL_CHARS, "850_JSON", "Failed")
    with pytest.raises(BusinessError) as e:
        await go(env.factory, DOCUMENT_ACTIONS, "ignore", did, None, rt)
    assert e.value.code == "reason.required"
    assert (await go(env.factory, DOCUMENT_ACTIONS, "ignore", did, None, rt, reason="a test file")).status == "done"
    [d] = await env.q("SELECT processing_status, error FROM sales_documents WHERE id = :i", i=did)
    assert d["processing_status"] == "Skipped" and "a test file" in d["error"]
    for id, kw in (("ignore", {"reason": "again"}), ("read_again", {})):
        r = await go(env.factory, DOCUMENT_ACTIONS, id, did, None, rt, **kw)
        assert r.status == "skipped" and "Skipped" in r.reason  # no version: its status refuses the repeat
    other = await _stored(env, b"{}", "850_JSON", "Failed")
    assert (await go(env.factory, DOCUMENT_ACTIONS, "read_again", other, None, rt)).status == "done"


async def test_a_count_applied_once_and_undone_with_why(p7):  # noqa: F811
    await _received(p7)
    c = await _upload(p7, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert (await go(p7.factory, COUNT_ACTIONS, "apply", c.id, c.version)).status == "done"
    assert (await _lots(p7))[("CF0092", "26C253125110231")] == 8000
    r = await go(p7.factory, COUNT_ACTIONS, "apply", c.id, c.version)
    assert r.status == "skipped" and "already the start" in r.reason
    [row] = await p7.q("SELECT version FROM sales_stock_counts WHERE id = :i", i=c.id)
    r = await go(p7.factory, COUNT_ACTIONS, "undo", c.id, row["version"] - 1, reason="wrong moment")
    assert (r.status, r.code) == ("failed", "entity.stale")
    assert (await go(p7.factory, COUNT_ACTIONS, "undo", c.id, row["version"], reason="wrong moment")).status == "done"
    assert (await _lots(p7))[("CF0092", "26C253125110231")] == 8400


@pytest.mark.parametrize(("registry", "id"), [(r, a) for r in (TRANSFER_ACTIONS, DOCUMENT_ACTIONS, COUNT_ACTIONS) for a in r.actions],
                         ids=lambda x: x if isinstance(x, str) else "")
async def test_each_is_one_at_a_time_and_needs_its_permission(env, monkeypatch, registry, id):  # noqa: F811
    async with env.factory(OP) as u:
        with pytest.raises(InvalidInput) as e:
            await run_action(u, registry, id, ActionIn(ids={1: 1, 2: 1}, reason="why not"))
        assert e.value.code == "action.single"

    async def reader(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    monkeypatch.setattr(env.factory, "permissions", reader)
    async with env.factory(OP) as u:
        with pytest.raises(PermissionDenied):
            await run_action(u, registry, id, ActionIn(ids={1: 1}, reason="why not"))
