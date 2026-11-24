"""Resending a transfer's 943 (stage 3 h): once, by the version seen, never past a 943 still under way. The fake SFTP behind
the real guard."""
from types import SimpleNamespace

import pytest

from app.models.config import Connection
from app.models.transfers import Transfer
from app.platform.actors import SYSTEM_ACTOR
from app.platform.authorization import PermissionDenied
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.commands import Stale
from app.platform.errors import InvalidInput
from app.platform.outbox import AcceptedThenFailed
from app.services.settings import SETTINGS, SHADOW_MODE
from app.services.transfer_actions import TRANSFER_ACTIONS
from app.services.transfer_views import TransferService
from app.services.transfers import SEND_943
from tests.sending import STATE_IDS, STATES, put_row, rows, said, says, set_row, twice, way_out
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import create, p7, sent_transfer  # noqa: F401 — p7 is a fixture


def puts(t):
    return [c for c, _ in t.store.calls if c == "put"]


async def tr(t, x):
    return (await t.q("SELECT status, version FROM sales_transfers WHERE id = :i", i=x.id))[0]


async def failed(t):
    """A transfer whose 943 could not be put."""
    t.store.fail["put"] = RuntimeError("connection reset")
    x = await sent_transfer(t)
    del t.store.fail["put"]
    assert (await tr(t, x))["status"] == "Failed"
    t.store.calls.clear()
    return x


def press(t, x, v):
    async def go():
        async with t.factory(OP) as u:
            await TransferService(u).resend(t.tr, x.id, v)
            await u.commit()
    return go


async def act(t, x, v):
    async with t.factory(OP) as u:
        out = await run_action(u, TRANSFER_ACTIONS, "resend", ActionIn(ids={x.id: v}), SimpleNamespace(transfers=t.tr))
        await u.commit()
    return out.results[0]


async def n_943(t):
    return [r["st02"] for r in await t.q("SELECT st02 FROM sales_documents WHERE kind = '943_EDI' ORDER BY id")]


@pytest.mark.parametrize(("status", "minutes", "expected"), STATES, ids=STATE_IDS)
async def test_a_resend_and_a_943_in_each_state(p7, status, minutes, expected):  # noqa: F811
    x = await failed(p7)
    await put_row(p7, SEND_943, {"document_id": 1, "transfer_id": x.id}, ("sales_transfers", x.id), status, minutes)
    e = await says(press(p7, x, (await tr(p7, x))["version"]))
    assert said(e) == expected
    if expected and expected[1] == "unknown":
        assert way_out(e)
    assert len(await n_943(p7)) == (1 if expected else 2)  # refused: no file, no control number


async def test_another_transfer_s_943_does_not_block(p7):  # noqa: F811
    x = await failed(p7)
    await put_row(p7, SEND_943, {}, ("sales_transfers", x.id + 1), "PENDING")
    await put_row(p7, "notify_email", {}, ("sales_transfers", x.id), "PENDING")
    assert await says(press(p7, x, (await tr(p7, x))["version"])) is None


async def test_a_943_lost_in_a_crash_says_how_it_ends_then_can_be_resent(p7):  # noqa: F811
    x = await create(p7)
    async with p7.factory(OP) as u:
        await TransferService(u).ready(p7.tr, x.id, (await tr(p7, x))["version"])
        await u.commit()
    await p7.tr.queue_ready()
    [r] = await rows(p7, SEND_943)
    await set_row(p7, r["id"], status="IN_FLIGHT")  # claimed; the process died before its outcome: its lease is over
    e = await says(press(p7, x, (await tr(p7, x))["version"]))  # the transfer says 943 Generated: that alone would not say what to do
    assert said(e) == ("send.outcome_unknown", "unknown") and way_out(e)
    assert (await p7.worker.run_once()).failed == 1 and puts(p7) == []  # closed, not run again
    assert (await tr(p7, x))["status"] == "Failed"
    assert await says(press(p7, x, (await tr(p7, x))["version"])) is None
    assert (await p7.worker.run_once()).done == 1 and len(puts(p7)) == 1


async def test_two_presses_at_once_make_one_943(p7):  # noqa: F811
    x = await failed(p7)
    v = (await tr(p7, x))["version"]
    e = await twice(p7, OP, lambda u: TransferService(u).resend(p7.tr, x.id, v))
    assert said(e) == ("send.queued", "pending")
    ctls = await n_943(p7)
    assert len(ctls) == 2 == len(set(ctls)) and [r["status"] for r in await rows(p7, SEND_943)] == ["FAILED", "PENDING"]
    await p7.worker.run_once()
    assert len(puts(p7)) == 1


async def test_by_the_version_seen_and_a_failed_one_again(p7):  # noqa: F811
    x = await failed(p7)
    v = (await tr(p7, x))["version"]
    assert isinstance(await says(press(p7, x, v - 1)), Stale) and isinstance(await says(press(p7, x, None)), InvalidInput)
    assert len(await n_943(p7)) == 1
    p7.store.fail["put"] = RuntimeError("again")
    assert await says(press(p7, x, v)) is None
    assert (await p7.worker.run_once()).failed == 1
    del p7.store.fail["put"]
    assert isinstance(await says(press(p7, x, v)), Stale)  # failed once more since
    assert await says(press(p7, x, (await tr(p7, x))["version"])) is None
    await p7.worker.run_once()
    assert (await tr(p7, x))["status"] == "Waiting for 997"
    assert (await says(press(p7, x, (await tr(p7, x))["version"]))).code == "transfer.not_failed"


async def test_a_refused_put_can_be_resent_and_a_delivered_one_is_not(p7):  # noqa: F811
    x = await failed(p7)

    async def can_write(on):
        async with p7.factory(SYSTEM_ACTOR) as u:
            (await u.session.get(Connection, p7.edi.conn_id)).can_write = on
            await u.commit()
    await can_write(False)
    assert await says(press(p7, x, (await tr(p7, x))["version"])) is None
    assert (await p7.worker.run_once()).suppressed == 1 and (await tr(p7, x))["status"] == "Failed" and puts(p7) == []
    await can_write(True)
    p7.store.fail["put"] = AcceptedThenFailed("delivered, then the line dropped")
    assert await says(press(p7, x, (await tr(p7, x))["version"])) is None
    assert (await p7.worker.run_once()).done == 1 and (await tr(p7, x))["status"] == "Waiting for 997"
    assert (await says(press(p7, x, (await tr(p7, x))["version"]))).code == "transfer.not_failed"
    assert (await p7.worker.run_once()).processed == 0 and len(puts(p7)) == 1  # never again by itself


async def test_each_refusal_stands_and_makes_nothing(p7, monkeypatch):  # noqa: F811
    x = await failed(p7)
    v = (await tr(p7, x))["version"]
    async with p7.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await u.commit()
    assert (await says(press(p7, x, v))).code == "transfer.shadow"
    assert (await act(p7, x, v)).code == "transfer.shadow"
    async with p7.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, False)
        (await u.session.get(Connection, p7.edi.conn_id)).active = False
        await u.commit()
    assert (await says(press(p7, x, v))).code == "edi.no_connection"
    async with p7.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, p7.edi.conn_id)).active = True
        row = await u.session.get(Transfer, x.id)
        row.status = "Cancelled"
        await u.commit()
    assert (await says(press(p7, x, v + 1))).code == "transfer.not_failed"
    assert (await act(p7, x, v + 1)).status == "skipped"
    async with p7.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Transfer, x.id)).status = "Failed"
        await u.commit()
    was = p7.factory.permissions

    async def reader(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    monkeypatch.setattr(p7.factory, "permissions", reader)
    assert isinstance(await says(press(p7, x, v)), PermissionDenied)
    with pytest.raises(PermissionDenied):
        await act(p7, x, v)
    monkeypatch.setattr(p7.factory, "permissions", was)
    assert len(await n_943(p7)) == 1 and puts(p7) == []
    v = (await tr(p7, x))["version"]
    r = await act(p7, x, v - 1)
    assert (r.status, r.code) == ("failed", "entity.stale")
    assert (await act(p7, x, v)).status == "done"
    r = await act(p7, x, v)
    assert r.status == "skipped" and "already queued" in r.reason
