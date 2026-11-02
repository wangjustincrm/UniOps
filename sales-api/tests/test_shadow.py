"""The shadow run on the 940 side and the comparison (§5.4; reduced scope,
2026-11-02). The fixture is P4's: writes ON, a LIVE connection, its host a
write host — so a put that happened would be visible in the fake store."""
from datetime import timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa

from app.domain.edi940 import build_940
from app.domain.status import Fulfillment as F
from app.models.config import Partner
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.services.outbound import TORONTO
from app.services.settings import SETTINGS, SHADOW_MODE
from app.services.shadow import ShadowMatchService, ShadowRun
from tests.test_orders import OP, T0, env  # noqa: F401 — env is a fixture (p4_edi needs it)
from tests.test_outbound import a_paid_order, ack997, send_now, sent_order, status, store_997
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture


@pytest_asyncio.fixture
async def edi(p4_edi):  # noqa: F811 — the P4 fixture, under the name these tests use
    return p4_edi


async def shadow(edi, on=True):
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, on)
        await u.commit()


async def outbox(edi):
    return await edi.env.q("SELECT kind, status FROM sales_outbox ORDER BY id")


# ── nothing leaves ───────────────────────────────────────────────────────────

async def test_with_writes_on_the_shadow_run_puts_nothing(edi):
    queued_before = await a_paid_order(edi, number=9301)
    await edi.out.queue_due()                       # an outbox row from before the switch-on
    await shadow(edi)
    assert (await edi.worker.run_once()).cancelled == 1  # the second barrier
    o = await a_paid_order(edi, number=9302)
    assert o.shadow
    r = await edi.out.queue_due()
    assert (r.queued, r.skipped) == (0, "shadow run: no 940 is generated")
    with pytest.raises(BusinessError) as e:
        await send_now(edi, o.id)
    assert e.value.code == "send940.shadow"
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        assert await edi.out.preview(u.session) == []
    assert edi.store.files == {} and edi.store.calls == []       # zero puts, zero traffic
    assert await outbox(edi) == [{"kind": "send_940", "status": "CANCELLED"}]  # none added
    assert await status(edi, queued_before.id) == F.GENERATED_940
    log = await edi.env.q("SELECT message FROM sales_log WHERE message LIKE 'shadow run: 940%'")
    assert len(log) == 1


async def test_an_order_received_during_the_shadow_run_never_gets_a_940_afterwards(edi):
    await shadow(edi)
    o = await a_paid_order(edi)
    await shadow(edi, False)
    assert (await edi.out.queue_due()).queued == 0  # Zoho handled it
    with pytest.raises(BusinessError) as e:
        await send_now(edi, o.id)
    assert e.value.code == "send940.shadow_order"
    edi.env.shop.orders = []
    other = await a_paid_order(edi, number=9302)  # received after the switch-off: the normal path
    assert not other.shadow and (await edi.out.queue_due()).queued == 1


async def test_no_task_is_raised_for_order_work_only_logged(edi):
    await shadow(edi)
    edi.env.shop.orders = []
    await a_paid_order(edi, lines=[{"id": 11, "sku": "NOPE", "title": "x", "variant_title": "Case", "quantity": 1,
                                    "price": "1", "requires_shipping": True}])
    o, _ = await edi.env.order(1)
    assert o.fulfillment_status == F.REVIEW
    assert await edi.env.q("SELECT 1 FROM tasks") == []
    assert await edi.env.q("SELECT 1 FROM sales_log WHERE message LIKE 'shadow run: no task raised:%'")


async def test_a_997_in_the_shadow_run_is_stored_only(edi):
    o, d = await sent_order(edi)
    await shadow(edi)
    await store_997(edi, ack997(d["st02"], "R", "R", "5"))
    r = await edi.out.process_997s()
    assert (r.shadow_997, r.acks) == (1, 0)
    [ack] = await edi.env.q("SELECT processing_status, shadow, error FROM sales_documents WHERE kind = '997_EDI'")
    assert (ack["processing_status"], ack["shadow"]) == ("Skipped", True) and "stored only" in ack["error"]
    assert await status(edi, o.id) == F.SENT_940 and await edi.env.q("SELECT 1 FROM tasks") == []


# ── the reference folder and the comparison ─────────────────────────────────

async def zoho_940(edi, order_id: int, *, change=None, ctl=16468) -> bytes:
    """What Zoho would have sent: Sales' builder at another time and number, then edited."""
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        order = await u.session.get(Order, order_id)
        partner = (await u.session.execute(sa.select(Partner).where(Partner.code == order.partner_code))).scalar_one()
        snap = await edi.out.snapshot(u.session, order, partner)
    data = build_940(snap, ctl, (T0 - timedelta(days=1)).astimezone(TORONTO)).content
    return change(data) if change else data


def run(edi, folder):
    return ShadowRun(uow_factory=edi.env.factory, outbound=edi.out, reference_dir=str(folder), clock=lambda: edi.clock.now)


async def matches(edi):
    return await edi.env.q("SELECT status, order_id, reference_number, differences, note FROM sales_shadow_matches "
                           "ORDER BY reference_document_id NULLS LAST, id")


async def test_reference_files_are_stored_once_as_documents_only(edi, tmp_path):
    await shadow(edi)
    o = await a_paid_order(edi)
    (tmp_path / "940s").mkdir()
    (tmp_path / "940s" / "z1.edi").write_bytes(await zoho_940(edi, o.id))
    (tmp_path / "notes.txt").write_bytes(b"not an EDI file")
    r1 = await run(edi, tmp_path).run()
    r2 = await run(edi, tmp_path).run()
    assert (r1.imported, r2.imported, r2.already_here) == (2, 0, 2)
    refs = await edi.env.q("SELECT direction, origin, kind, remote_name, shadow, file_status, processing_status "
                           "FROM sales_documents WHERE origin = 'ZOHO_REFERENCE' ORDER BY remote_name")
    assert [(x["direction"], x["kind"], x["remote_name"], x["shadow"]) for x in refs] == [
        ("OUT", "940_EDI", "940s/z1.edi", True), ("OUT", "OTHER", "notes.txt", True)]
    assert all(x["file_status"] is None and x["processing_status"] is None for x in refs)  # never sent, never parsed
    assert await edi.env.q("SELECT count(*) n FROM sales_orders") == [{"n": 1}]
    assert await outbox(edi) == [] and edi.store.calls == []


async def test_match_differs_no_sales_order_and_sales_only(edi, tmp_path):
    await shadow(edi)
    edi.env.shop.orders = []
    same = await a_paid_order(edi, number=9301)
    edi.env.shop.orders = []
    other = await a_paid_order(edi, number=9302)
    edi.env.shop.orders = []
    alone = await a_paid_order(edi, number=9303)  # no Zoho file: Sales only
    (tmp_path / "a.edi").write_bytes(await zoho_940(edi, same.id))
    (tmp_path / "b.edi").write_bytes(await zoho_940(edi, other.id, change=lambda d: d.replace(b"W01*12*EA", b"W01*18*EA")))
    (tmp_path / "c.edi").write_bytes(await zoho_940(edi, same.id, change=lambda d: d.replace(b"W05*N*9301", b"W05*N*7777")))
    seq = "SELECT last_value, is_called FROM sales_x12_control_seq"
    before = await edi.env.q(seq)
    r = await run(edi, tmp_path).run()
    assert r.by_status == {"Match": 1, "Differs": 1, "No Sales order": 1, "Sales only": 1}
    got = await matches(edi)
    assert [(m["status"], m["order_id"], m["reference_number"], m["differences"]) for m in got] == [
        ("Match", same.id, "9301", 0), ("Differs", other.id, "9302", 1), ("No Sales order", None, "7777", 0),
        ("Sales only", alone.id, "9303", 0)]
    assert await outbox(edi) == [] and edi.store.calls == []  # dry runs only
    assert await edi.env.q(seq) == before  # the real control sequence is never touched


async def test_an_expected_difference_holds_while_the_differences_stay_the_same(edi, tmp_path):
    await shadow(edi)
    o = await a_paid_order(edi)
    (tmp_path / "b.edi").write_bytes(await zoho_940(edi, o.id, change=lambda d: d.replace(b"W01*12*EA", b"W01*18*EA")))
    await run(edi, tmp_path).run()
    [m] = await edi.env.q("SELECT id, version FROM sales_shadow_matches")
    async with edi.env.factory(OP) as u:
        view = await ShadowMatchService(u).compare_view(m["id"])
        assert [(row.zoho, row.sales) for row in view.rows if not row.same] == [("W01*18*EA**VN*CF0092", "W01*12*EA**VN*CF0092")]
        assert "ISA ISA09, ISA10, ISA13" in view.ignored
        out = await ShadowMatchService(u).mark_expected(m["id"], "Zoho rounds cases up", m["version"])
        await u.commit()
    assert out.status == "Expected difference"
    audit = await edi.env.q("SELECT reason FROM sales_audit WHERE entity = 'sales_shadow_matches' AND reason LIKE 'expected%'")
    assert audit and "Zoho rounds cases up" in audit[0]["reason"]
    await run(edi, tmp_path).run()
    assert [x["status"] for x in await matches(edi)] == ["Expected difference"]  # the same difference: still expected
    async with edi.env.factory(SYSTEM_ACTOR) as u:  # Sales now builds something else
        order = await u.session.get(Order, o.id)
        order.ship_to = {**order.ship_to, "city": "Laval"}
        await u.commit()
    await run(edi, tmp_path).run()
    assert [(x["status"], x["differences"]) for x in await matches(edi)] == [("Differs", 2)]
    async with edi.env.factory(OP) as u:
        summary = await ShadowMatchService(u).summary()
    assert summary["Differs"] == 1 and summary["Expected difference"] == 0


async def test_marking_needs_the_operate_permission(edi, tmp_path):
    from app.platform.authorization import PermissionDenied
    await shadow(edi)
    o = await a_paid_order(edi)
    (tmp_path / "b.edi").write_bytes(await zoho_940(edi, o.id, change=lambda d: d.replace(b"W01*12*EA", b"W01*18*EA")))
    await run(edi, tmp_path).run()
    [m] = await edi.env.q("SELECT id FROM sales_shadow_matches")

    async def read_only(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    edi.env.factory.permissions = read_only
    with pytest.raises(PermissionDenied):
        async with edi.env.factory(OP) as u:
            await ShadowMatchService(u).mark_expected(m["id"], "because", None)
    assert [x["status"] for x in await matches(edi)] == ["Differs"]


async def test_the_switch_needs_sales_config_and_is_audited(edi):
    from app.platform.authorization import PermissionDenied
    from app.schemas.connections import ShadowSetting
    from app.services.connections import ShadowSettingService
    async with edi.env.factory(OP) as u:
        assert (await ShadowSettingService(u).get()).shadow_mode is False
        await ShadowSettingService(u).update(ShadowSetting(shadow_mode=True))
        await u.commit()
    async with edi.env.factory(OP) as u:
        assert (await ShadowSettingService(u).get()).shadow_mode is True
    assert await edi.env.q("SELECT 1 FROM sales_audit WHERE entity = 'sales_settings' AND reason = 'shadow run on'")

    async def read_only(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    edi.env.factory.permissions = read_only
    with pytest.raises(PermissionDenied):
        async with edi.env.factory(OP) as u:
            await ShadowSettingService(u).update(ShadowSetting(shadow_mode=False))
