"""Cancelling a transfer while its 943 is being sent (F1, review of stage 3, 2026-11-24): refused, as an order's is; a queued one
is cancelled; and neither the outbox nor the cancellation waits for the other."""
import asyncio
from types import SimpleNamespace

import sqlalchemy as sa

from app.models.transfers import Transfer
from app.platform.actors import SYSTEM_ACTOR
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.commands import ANY_VERSION
from app.platform.errors import BusinessError
from app.services.transfer_actions import TRANSFER_ACTIONS
from app.services.transfers import SEND_943
from tests import acting
from tests.sending import crash, rows, says, way_out
from tests.test_lock_order import meets, queued_943, state
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import create, p7, sent_transfer  # noqa: F401 — p7 is a fixture


def cancel(p7, x):  # noqa: F811
    async def go():
        async with p7.factory(OP) as u:
            await acting.transfer(u, p7.tr, "cancel", x.id, ANY_VERSION, "wrong lines")
            await u.commit()
    return go


async def cancel_action(p7, x):  # noqa: F811
    v = (await p7.q("SELECT version FROM sales_transfers WHERE id = :i", i=x.id))[0]["version"]
    async with p7.factory(OP) as u:
        out = await run_action(u, TRANSFER_ACTIONS, "cancel", ActionIn(ids={x.id: v}, reason="wrong lines"), SimpleNamespace(transfers=p7.tr))
        await u.commit()
    return out.results[0]


async def test_a_queued_943_is_cancelled_with_its_transfer(p7):  # noqa: F811
    x, _ = await queued_943(p7)
    assert await says(cancel(p7, x)) is None
    s = await state(p7, x)
    assert (s[0], s[2], s[3]) == ("Cancelled", "Cancelled", ["CANCELLED"])
    assert (await p7.worker.run_once()).processed == 0 and p7.store.files == {}


async def test_a_943_being_sent_is_not_cancelled_claim_first(p7):  # noqa: F811
    x, _ = await queued_943(p7)
    _, claim = await p7.worker._claim()  # committed: it is being sent
    before = await state(p7, x)
    assert before[0] == "943 Generated" and before[2:] == ("Sending", ["IN_FLIGHT"])
    e = await says(cancel(p7, x))
    assert (e.code, e.details) == ("transfer.sending", {"state": "sending"}) and "being sent right now" in e.message
    r = await cancel_action(p7, x)
    assert (r.status, r.reason) == ("skipped", e.message)
    assert await state(p7, x) == before  # nothing cancelled, no status, no document
    assert await p7.worker._record(claim, None) == "done"
    assert (await state(p7, x))[0] == "Waiting for 997"


async def test_a_cancellation_waits_for_a_claim_not_yet_committed_then_is_refused(p7):  # noqa: F811
    x, _ = await queued_943(p7)
    async with p7.factory(SYSTEM_ACTOR) as u:
        [r] = await rows(p7, SEND_943)
        payload = (await p7.q("SELECT payload FROM sales_outbox WHERE id = :i", i=r["id"]))[0]["payload"]
        await u.session.execute(sa.text("SELECT 1 FROM sales_outbox WHERE id = :i FOR UPDATE"), {"i": r["id"]})
        assert await p7.tr._pre_check(payload, u) is None  # as the claim does: the row, then the transfer and its 943
        await u.session.execute(sa.text("UPDATE sales_outbox SET status = 'IN_FLIGHT', next_attempt_at = now() + interval '15 minutes' WHERE id = :i"), {"i": r["id"]})
        task = asyncio.create_task(says(cancel(p7, x)))
        await asyncio.sleep(0.4)
        assert not task.done()
        await u.commit()
    e = await asyncio.wait_for(task, 10)
    assert e.code == "transfer.sending" and (await state(p7, x))[0] == "943 Generated"


async def test_a_cancellation_and_a_claim_waiting_for_it_cancel_first(p7):  # noqa: F811
    """The claim holds its row and waits for the transfer; the cancellation must not wait for that row."""
    x, d = await queued_943(p7)
    out = await meets(p7, Transfer, x.id, d, p7.worker._claim, lambda u: acting.transfer(u, p7.tr, "cancel", x.id, ANY_VERSION, "wrong lines"))
    assert out == ("cancelled", None)  # it read the transfer again: cancelled
    s = await state(p7, x)
    assert (s[0], s[2], s[3]) == ("Cancelled", "Cancelled", ["CANCELLED"]) and p7.store.files == {}


async def test_a_943_lost_in_a_crash_is_not_cancelled_until_its_row_is_closed(p7):  # noqa: F811
    x, _ = await queued_943(p7)
    [r] = await rows(p7, SEND_943)
    await crash(p7, r["id"])
    before = await state(p7, x)
    e = await says(cancel(p7, x))
    assert (e.code, e.details) == ("transfer.sending", {"state": "unknown"}) and way_out(e) and "before cancelling" in e.message
    assert (await cancel_action(p7, x)).status == "skipped" and await state(p7, x) == before
    assert (await p7.worker.run_once()).failed == 1  # closed by the next pass
    assert await says(cancel(p7, x)) is None and (await state(p7, x))[0] == "Cancelled"


async def test_the_cancellation_s_other_rules_stand(p7):  # noqa: F811
    x = await sent_transfer(p7)
    assert (await says(cancel(p7, x))).code == "transfer.not_cancellable"
    draft = await create(p7)

    async def stale():
        async with p7.factory(OP) as u:
            await acting.transfer(u, p7.tr, "cancel", draft.id, 99, "wrong lines")
    assert (await says(stale)).code == "entity.stale"

    async def gone():
        async with p7.factory(OP) as u:
            await acting.transfer(u, p7.tr, "cancel", 99_999, ANY_VERSION, "wrong lines")
    assert isinstance(await says(gone), BusinessError)
