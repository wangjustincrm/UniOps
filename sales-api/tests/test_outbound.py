"""P4: 940 / 997 (FR-OB-03c/d, 16, 20, 22, 22a, 23, 24, 29a; Appendix A).
The SFTP gateway is the real guard around a fake store (a write needs the
switch, a LIVE connection and a write host); orders come in through the P3
fixture. AC-OB-25 (segment-by-segment replay of archived Zoho 940s) waits for
real files."""
import asyncio
import random
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

from app.container import build_container
from app.domain.order_rules import facts_of, qualifies
from app.domain.status import Fulfillment as F
from app.domain.x12 import segments
from app.integrations.email import FakeEmail
from app.integrations.sftp import FakeSftp
from app.models.config import Connection, Item, Partner
from app.models.documents import Document, DocumentLink
from app.models.orders import Order
from app.models.records import OutboxRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.outbox import Outbox, OutboxWorker
from app.services.order_views import OrderService
from app.services.outbound import SEND_940, Outbound940, eligible_clause
from app.services.settings import EDI_CONNECTION_ID, SETTINGS
from tests.test_orders import OP, T0, env, po, run_850, shopify_order  # noqa: F401 — env is a fixture

AFTER_DELAY = T0 + timedelta(hours=4)  # SHOP's delay is 180 minutes from payment


@pytest_asyncio.fixture
async def edi(env):  # noqa: F811
    async with env.factory(SYSTEM_ACTOR) as u:
        s = u.session
        await s.execute(sa.text("TRUNCATE sales_outbox RESTART IDENTITY"))
        for code, w in (("CF0092", "2.03"), ("CF0010", "2.48")):
            (await s.execute(sa.select(Item).where(Item.item_code == code))).scalar_one().gross_weight_lb_per_ea = Decimal(w)
        s.add(Partner(code="MNL", name="Manual sender", abbrev="MNL", channel="SHOPIFY", settlement_mode="PREPAID",
                      sending_mode="MANUAL", delay_minutes=0, invoice_method="NONE", dc_routing_required=False, version=1))
        conn = Connection(name="radiant", kind="SFTP", host="commhub.example", port=22, username="crm", after_read="LEAVE",
                          mode="LIVE", secret_name="SALES_SECRET_RADIANT", inbound_folder="/prod/out",
                          outbound_folder="/prod/in", test_outbound_folder="/test/in", stable_seconds=0, version=1)
        s.add(conn)
        await s.flush()
        await SETTINGS.set(s, EDI_CONNECTION_ID, conn.id)
        await u.commit()
    store, refused = FakeSftp(), []

    async def sink(r):
        refused.append(r)
    settings = SimpleNamespace(ENVIRONMENT="production", sftp_write_hosts=frozenset({"commhub.example"}),
                               SALES_PARTNER_WRITES_ENABLED=True, SALES_EXTERNAL_EMAIL_ENABLED=False)
    gateways = build_container(settings, real_sftp=lambda: store, real_email=FakeEmail, real_shopify=lambda: env.shop,
                               refusal_sink=sink)
    clock = SimpleNamespace(now=AFTER_DELAY)
    out = Outbound940(uow_factory=env.factory, tasks=env.tasks, sftp=gateways.sftp, clock=lambda: clock.now)
    worker = OutboxWorker(Outbox(OutboxRecord), {SEND_940: out.outbox_kind()}, env.factory)  # real time: rows are stamped now()
    return SimpleNamespace(env=env, out=out, worker=worker, store=store, settings=settings, refused=refused, clock=clock,
                           conn_id=conn.id, gateways=gateways)


async def a_paid_order(edi, number=9301, lines=None):
    edi.env.shop.orders = [shopify_order(number - 9300, number, lines=lines or [
        {"id": 11, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Case", "quantity": 2, "price": "50",
         "requires_shipping": True}])]
    await edi.env.pull()
    o, _ = await edi.env.order(number - 9300)
    return o


async def docs(edi, kind="940_EDI"):
    return await edi.env.q("SELECT id, file_status, ack_status, st02, sent_at, error, remote_name FROM sales_documents "
                           "WHERE kind = :k ORDER BY id", k=kind)


async def tasks_of(edi, type_):
    return await edi.env.q("SELECT description, is_completed FROM tasks WHERE type = :t ORDER BY created_at", t=f"sales_{type_}")


async def status(edi, oid):
    return (await edi.env.q("SELECT fulfillment_status FROM sales_orders WHERE id = :i", i=oid))[0]["fulfillment_status"]


# ── eligibility: the SQL twin equals qualifies() ─────────────────────────────

async def test_the_sql_twin_selects_exactly_what_qualifies_selects(edi):
    rnd = random.Random(4)
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        partners = {p.code: p for p in (await u.session.execute(sa.select(Partner))).scalars()}
        for i in range(400):
            p = rnd.choice(["SHOP", "LBL", "MAN", "MNL"])
            u.session.add(Order(
                partner_code=p, source_type=rnd.choice(["SHOPIFY", "EDI_850", "MANUAL"]), source_order_id=f"g{i}",
                fulfillment_status=rnd.choice([F.PENDING, F.PENDING, F.REVIEW, F.PAYMENT_HOLD, F.SENT_940]).value,
                invoice_status=rnd.choice(["Pending", "Not Required", "Blocked", "Failed"]),
                shopify_financial_status=rnd.choice(["paid", "pending", "partially_refunded", "refunded", None]),
                shopify_fulfillment_status=rnd.choice([None, None, "fulfilled", "partial"]),
                archived_in_shopify_at=rnd.choice([None, None, T0]), historical=rnd.random() < 0.2, shadow=rnd.random() < 0.15,
                first_received_at=T0 - timedelta(minutes=rnd.randint(0, 600)),
                paid_at=rnd.choice([None, T0 - timedelta(minutes=rnd.randint(0, 600))]), version=1))
        await u.commit()
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        rows = (await u.session.execute(sa.select(Order).where(Order.source_order_id.like("g%")))).scalars().all()
        expected = {o.id for o in rows if qualifies(*facts_of(o, partners[o.partner_code]), T0)[0]}
        got = set((await u.session.execute(sa.select(Order.id).join(Partner, Partner.code == Order.partner_code)
                                           .where(eligible_clause(T0), Order.source_order_id.like("g%")))).scalars())
    assert got == expected and 3 < len(expected) < 390  # both sides decided, on a real mix


async def test_ac21_the_dry_run_queues_no_historical_order_and_writes_nothing(edi):
    o = await a_paid_order(edi)
    old = await a_paid_order(edi, number=8001)  # below the cut-off 9000: historical
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        preview = await edi.out.preview(u.session)
    assert [p["order_id"] for p in preview] == [o.id] and old.historical
    assert await docs(edi) == []


# ── generate and send ────────────────────────────────────────────────────────

async def test_an_eligible_order_gets_its_940_put_through_the_guard(edi):
    o = await a_paid_order(edi)
    edi.clock.now = T0 + timedelta(minutes=10)
    assert (await edi.out.queue_due()).queued == 0  # the delay has not run (FR-OB-03d)
    edi.clock.now = AFTER_DELAY
    r = await edi.out.queue_due()
    assert r.queued == 1 and await status(edi, o.id) == F.GENERATED_940
    [d] = await docs(edi)
    assert (d["file_status"], d["ack_status"]) == ("Ready To Send", "Waiting")
    assert (await edi.worker.run_once()).done == 1
    [d] = await docs(edi)
    assert (d["file_status"], d["ack_status"], d["sent_at"] is not None) == ("Sent", "Waiting", True)
    assert await status(edi, o.id) == F.SENT_940  # AC-OB-31: sent, not yet acknowledged
    [(path, content)] = edi.store.files.items()
    assert path == f"/prod/in/{d['remote_name']}" and d["remote_name"].startswith(f"940-SHOP-{o.order_number:09d}-9301-")
    w01 = [s for s in segments(content) if s[0] == "W01"]
    assert w01 == [["W01", "12", "EA", "", "VN", "CF0092"]]  # 2 cases × 6 (AC-OB-14 for Shopify)
    assert (await edi.out.queue_due()).queued == 0  # sent once


async def test_a_write_is_suppressed_without_live_or_without_a_write_host(edi):
    o = await a_paid_order(edi)
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, edi.conn_id)).mode = "TEST"
        await u.commit()
    await edi.out.queue_due()
    assert (await edi.worker.run_once()).suppressed == 1
    assert edi.store.files == {} and edi.store.calls == []  # nothing reached the store
    assert [d["file_status"] for d in await docs(edi)] == ["Suppressed"] and await status(edi, o.id) == F.GENERATED_940
    assert edi.refused[-1].context["reason"] == "connection not LIVE"
    [t] = await tasks_of(edi, "send_refused")  # P8: a refused send is a task, not only a log row
    assert f"the 940 for order {o.order_number} was not sent" in t["description"]
    # LIVE, switch on, but the host is not a write host (e.g. Radiant on a dev stack)
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, edi.conn_id)).mode = "LIVE"
        await u.commit()
    edi.settings.sftp_write_hosts = frozenset({"sftp-test"})
    o2 = await a_paid_order(edi, number=9302)
    await edi.out.queue_due()
    assert (await edi.worker.run_once()).suppressed == 1 and edi.store.files == {}
    assert edi.refused[-1].context["reason"] == "host not in SALES_SFTP_WRITE_HOSTS"
    assert await status(edi, o2.id) == F.GENERATED_940


async def test_ac30_a_940_that_fails_validation_is_not_sent_and_raises_a_task(edi):
    o = await a_paid_order(edi)
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        order = await u.session.get(Order, o.id)
        order.ship_to = {**order.ship_to, "zip": ""}
        await u.commit()
    r = await edi.out.queue_due()
    assert r.invalid == 1 and await status(edi, o.id) == F.FAILED_940
    [d] = await docs(edi)
    assert d["file_status"] == "Cancelled" and "ST.zip" not in d["error"] and "ship_to.zip is empty" in d["error"]
    assert (await edi.worker.run_once()).done == 0 and edi.store.files == {}
    [t] = await tasks_of(edi, "940_failed")
    assert "ship_to.zip is empty" in t["description"] and "Send 940 now" in t["description"]


async def test_ac29_concurrent_generations_never_share_a_control_number(edi):
    for n in range(9301, 9306):
        await a_paid_order(edi, number=n)
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        ids = list((await u.session.execute(sa.select(Order.id).order_by(Order.id))).scalars())

    async def gen(oid):
        async with edi.env.factory(SYSTEM_ACTOR) as u:
            order = await u.session.get(Order, oid, with_for_update=True)
            partner = (await u.session.execute(sa.select(Partner).where(Partner.code == order.partner_code))).scalar_one()
            d = await edi.out.generate(u, order, partner, reason="test")
            await u.commit()
            return d.st02
    controls = await asyncio.gather(*(gen(i) for i in ids))
    assert len(set(controls)) == len(ids) and all(len(c) == 9 for c in controls)


# ── 997 (FR-OB-22a) and timeout (FR-OB-23) ───────────────────────────────────

def ack997(control: str, ak5: str = "A", ak9: str = "A", reasons: str = "") -> bytes:
    return (f"ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261030*1500*U*00401*000016468*0*P*>~\n"
            f"GS*FA*9055651212*CANADAROYALMILK*20261030*1500*16468*X*006030~\nST*997*0001~\nAK1*OW*{int(control)}~\n"
            f"AK2*940*{control}~\nAK5*{ak5}{'*' + reasons if reasons else ''}~\nAK9*{ak9}*1*1*1~\nSE*6*0001~\n"
            f"GE*1*16468~\nIEA*1*000016468~\n").encode()


async def store_997(edi, data: bytes, name="997.edi"):
    import hashlib
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        u.session.add(Document(direction="IN", origin="PARTNER", system="RADIANT", kind="997_EDI", remote_name=name,
                               size=len(data), sha256=hashlib.sha256(data).hexdigest(), content=data,
                               processing_status="Pending", connection_id=edi.conn_id))
        await u.commit()


async def sent_order(edi, number=9301):
    o = await a_paid_order(edi, number=number)
    await edi.out.queue_due()
    await edi.worker.run_once()
    d = [x for x in await docs(edi) if x["file_status"] == "Sent"][-1]
    return o, d


async def test_ac32_a_997_is_matched_by_ak1_and_ak2_and_a_rejection_carries_its_reason(edi):
    o, d = await sent_order(edi)
    await store_997(edi, ack997(d["st02"].lstrip("0"), "R", "R", "5"), "997-r.edi")  # leading zeros dropped on their side
    r = await edi.out.process_997s()
    assert r.acks == 1 and await status(edi, o.id) == F.REJECTED_940
    [d2] = await docs(edi)
    assert d2["ack_status"] == "Rejected"
    [t] = await tasks_of(edi, "940_rejected")
    assert "AK5 R 5" in t["description"] and "not resent automatically" in t["description"]
    assert (await edi.out.queue_due()).queued == 0 and len(edi.store.files) == 1  # never resent by itself


async def test_an_accepted_997_and_one_that_matches_nothing(edi):
    o, d = await sent_order(edi)
    await store_997(edi, ack997(d["st02"]), "997-a.edi")
    await store_997(edi, ack997("000099999"), "997-x.edi")
    r = await edi.out.process_997s()
    assert (r.acks, r.unmatched_997) == (1, 1)
    assert await status(edi, o.id) == F.ACCEPTED_940 and (await docs(edi))[0]["ack_status"] == "Accepted"
    [t] = await tasks_of(edi, "997_unmatched")
    assert "matches 0 outbound 940s" in t["description"]


async def test_ac33_no_997_in_two_hours_is_a_timeout_then_a_late_997_still_applies(edi):
    o, d = await sent_order(edi)
    async with edi.env.factory(SYSTEM_ACTOR) as u:  # sent 2 h 1 min ago
        await u.session.execute(sa.update(Document).where(Document.id == d["id"]).values(sent_at=AFTER_DELAY - timedelta(minutes=121)))
        await u.commit()
    assert (await edi.out.timeouts()).timeouts == 1 and await status(edi, o.id) == F.TIMEOUT_940
    [t] = await tasks_of(edi, "940_timeout")
    assert "No 997 arrived within 120 minutes" in t["description"] and "SHOP" in t["description"] and "Toronto time" in t["description"]
    assert (await edi.out.timeouts()).timeouts == 0  # once
    await store_997(edi, ack997(d["st02"]), "997-late.edi")
    await edi.out.process_997s()
    assert await status(edi, o.id) == F.ACCEPTED_940
    assert [x["is_completed"] for x in await tasks_of(edi, "940_timeout")] == [True]  # the late answer closes it


async def test_ac62_a_945_before_any_997_accepts_it_and_no_timeout_follows(edi):
    o, d = await sent_order(edi)
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        doc = await u.session.get(Document, d["id"], with_for_update=True)
        await edi.out.accept_by_945(u, doc, "945 control 77 arrived first")
        await u.session.execute(sa.update(Document).where(Document.id == d["id"]).values(sent_at=AFTER_DELAY - timedelta(hours=5)))
        await u.commit()
    assert (await docs(edi))[0]["ack_status"] == "Accepted by 945"
    assert (await edi.out.timeouts()).timeouts == 0


# ── Send 940 now (FR-OB-03c, AC-OB-24) ───────────────────────────────────────

async def send_now(edi, oid, replace=False):
    async with edi.env.factory(OP) as u:
        out = await OrderService(u).send_940_now(oid, edi.out, replace=replace)
        await u.commit()
        return out


async def test_ac24_manual_partner_send_now_live_940_and_replacement(edi):
    edi.env.shop.orders = []
    o = await a_paid_order(edi)
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, o.id)).partner_code = "MNL"
        await u.commit()
    assert (await edi.out.queue_due()).queued == 0  # a Manual-mode partner is never sent automatically
    edi.clock.now = T0 + timedelta(minutes=1)  # and Send now needs no delay
    first = await send_now(edi, o.id)
    assert first["file_status"] == "Ready To Send" and first["order"]["fulfillment_status"] == F.GENERATED_940
    with pytest.raises(BusinessError) as e:
        await send_now(edi, o.id)
    assert e.value.code == "send940.in_progress"
    await edi.worker.run_once()
    with pytest.raises(BusinessError) as e:
        await send_now(edi, o.id)
    assert e.value.code == "send940.live" and e.value.details["needs"] == "replace"
    second = await send_now(edi, o.id, replace=True)  # a confirmed replacement: new control number
    assert second["control"] != first["control"]
    assert [x["file_status"] for x in await docs(edi)] == ["Replaced", "Ready To Send"]


async def test_send_now_is_refused_for_a_historical_order_and_resends_after_a_rejection(edi):
    old = await a_paid_order(edi, number=8001)
    with pytest.raises(BusinessError) as e:
        await send_now(edi, old.id)
    assert e.value.code == "send940.historical"
    o, d = await sent_order(edi)
    await store_997(edi, ack997(d["st02"], "R", "R", "4"))
    await edi.out.process_997s()
    again = await send_now(edi, o.id)  # after a rejection: a new 940, a new number
    assert again["control"] != d["st02"] and [x["is_completed"] for x in await tasks_of(edi, "940_rejected")] == [True]


async def test_send_now_needs_the_operate_permission(edi):
    o = await a_paid_order(edi)
    reader = SimpleNamespace(user_id=OP.user_id, name="Reader", role="sales_reader", is_system=False)

    async def deny(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    edi.env.factory.permissions = deny
    from app.platform.authorization import PermissionDenied
    with pytest.raises(PermissionDenied):
        async with edi.env.factory(reader) as u:
            await OrderService(u).send_940_now(o.id, edi.out)
    assert await docs(edi) == []


# ── a source change after the 940 (FR-OB-16) ─────────────────────────────────

async def test_ac18_ac70_a_change_after_the_940_updates_the_order_and_raises_a_task_not_a_resend(edi):
    o, d = await sent_order(edi)
    edi.env.shop.orders = [shopify_order(1, 9301, updated=T0 + timedelta(hours=5), financial="partially_refunded", lines=[
        {"id": 11, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Case", "quantity": 2, "current_quantity": 1,
         "price": "50", "requires_shipping": True}])]
    await edi.env.pull(T0 + timedelta(hours=5))
    _, lines = await edi.env.order(1)
    assert lines[0].qty_ea == 6  # the order follows the source (#11)
    [t] = await tasks_of(edi, "changed_after_940")
    assert "line 11: 940 12 EA CF0092 → now 6 EA CF0092" in t["description"]
    assert await status(edi, o.id) == F.SENT_940 and len(await docs(edi)) == 1  # the 940 stays as sent; no resend
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        doc = await u.session.get(Document, d["id"])
        assert doc.body_json["locked"]["lines"] == {"11": {"item_code": "CF0092", "qty_ea": 12}}


# ── retailer 850 → 940 (AC-OB-14, 26) ────────────────────────────────────────

async def test_ac14_ac26_a_retail_940_renders_cases_in_ea_and_the_dc_fields(edi):
    await run_850(edi.env, {"PurchaseOrder": [po("WMT", "W-2", [
        {"Line": 1, "BuyerPart": "W-999", "Quantity": 10, "UOM": "CA"}], ship_to_gln="0012345000011")]})
    o, _ = await edi.env.order("W-2")
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        order = await u.session.get(Order, o.id)
        order.ship_to = {**order.ship_to, "address1": "1 DC Road", "city": "Mississauga", "province": "ON", "zip": "L5T 1A1"}
        await u.commit()
    edi.clock.now = o.first_received_at + timedelta(minutes=61)  # an 850 is stamped when read; WMT waits 60
    assert (await edi.out.queue_due()).queued == 1
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        content = (await u.session.execute(sa.select(Document.content).join(
            DocumentLink, DocumentLink.document_id == Document.id).where(DocumentLink.entity_id == o.id,
                                                                         Document.kind == "940_EDI"))).scalar_one()
    segs = segments(content)
    assert ["W01", "60", "EA", "", "VN", "CF0092", "BP", "W-999"] in segs  # AC-OB-14: 10 cases × 6 = W01*60*EA
    assert ["N9", "LO", "6080"] in segs and ["N9", "LB", "UCC_CRM_WMT"] in segs and ["N9", "DP"] in [s[:2] for s in segs]


async def test_ac63_archived_before_the_940_gets_none_and_unarchiving_queues_it_on_the_next_cycle(edi):
    later = T0 + timedelta(minutes=30)
    edi.env.shop.orders = [shopify_order(1, 9301, updated=later, closed_at=later.isoformat())]
    await edi.env.pull(later)
    o, _ = await edi.env.order(1)
    assert (await edi.out.queue_due()).queued == 0 and await docs(edi) == []
    assert [t["is_completed"] for t in await tasks_of(edi, "order_archived")] == [False]
    edi.env.shop.orders = [shopify_order(1, 9301, updated=T0 + timedelta(hours=1))]  # un-archived
    await edi.env.pull(T0 + timedelta(hours=1))
    assert (await edi.out.queue_due()).queued == 1 and await status(edi, o.id) == F.GENERATED_940
    assert [t["is_completed"] for t in await tasks_of(edi, "order_archived")] == [True]


async def test_ac70_the_940_ships_only_what_remains_and_never_a_non_shipping_line(edi):
    o = await a_paid_order(edi, lines=[
        {"id": 11, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Case", "quantity": 3, "current_quantity": 1,
         "price": "50", "requires_shipping": True},
        {"id": 12, "sku": None, "title": "Gift card", "variant_title": None, "quantity": 1, "price": "25",
         "requires_shipping": False, "gift_card": True}])
    assert (await edi.out.queue_due()).queued == 1
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        content = (await u.session.execute(sa.select(Document.content).join(
            DocumentLink, DocumentLink.document_id == Document.id).where(DocumentLink.entity_id == o.id,
                                                                         Document.kind == "940_EDI"))).scalar_one()
    assert [s for s in segments(content) if s[0] == "W01"] == [["W01", "6", "EA", "", "VN", "CF0092"]]


# ── the order's 940s and the EDI settings (P4 UI) ────────────────────────────

async def test_the_order_detail_shows_its_940_with_file_and_ack_status_apart(edi):
    o, d = await sent_order(edi)
    async with edi.env.factory(OP) as u:
        detail = await OrderService(u).detail(o.id)
    [doc] = [x for x in detail.documents if x.kind == "940_EDI"]
    assert (doc.direction, doc.file_status, doc.ack_status, doc.st02) == ("OUT", "Sent", "Waiting", d["st02"])
    assert doc.sent_at is not None


async def test_edi_settings_take_only_an_active_sftp_connection_and_need_config(edi):
    from app.platform.authorization import PermissionDenied
    from app.platform.errors import InvalidInput
    from app.schemas.connections import EdiSettings
    from app.services.connections import EdiSettingsService
    async with edi.env.factory(OP) as u:
        assert await EdiSettingsService(u).get() == EdiSettings(connection_id=edi.conn_id, ack_timeout_minutes=120,
                                                                 invoice_connection_id=None)
        shop = (await u.session.execute(sa.select(Connection.id).where(Connection.kind == "SHOPIFY").limit(1))).scalar()
        if shop is not None:
            with pytest.raises(InvalidInput):
                await EdiSettingsService(u).update(EdiSettings(connection_id=shop, ack_timeout_minutes=120))
        with pytest.raises(InvalidInput):
            await EdiSettingsService(u).update(EdiSettings(connection_id=999999, ack_timeout_minutes=120))
        await EdiSettingsService(u).update(EdiSettings(connection_id=None, ack_timeout_minutes=90))
        await u.commit()
    async with edi.env.factory(OP) as u:
        assert await EdiSettingsService(u).get() == EdiSettings(connection_id=None, ack_timeout_minutes=90)
    await a_paid_order(edi)
    r = await edi.out.queue_due()
    assert r.queued == 0  # no warehouse connection: fail closed

    async def read_only(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    edi.env.factory.permissions = read_only
    with pytest.raises(PermissionDenied):
        async with edi.env.factory(OP) as u:
            await EdiSettingsService(u).update(EdiSettings(connection_id=edi.conn_id, ack_timeout_minutes=120))


async def test_a_manual_order_with_its_ship_to_builds_a_valid_940(edi):
    from app.schemas.orders import AddressIn, ManualLineIn, ManualOrderIn
    from app.services.order_views import ManualOrderService
    async with edi.env.factory(OP) as u:
        o = await ManualOrderService(u).create(ManualOrderIn(
            partner_code="MAN", customer_reference="phone-7",
            ship_to=AddressIn(name="Staff shop", phone="613-555-0100", attention="J. Doe", address1="1680 Venture Dr", city="Kingston", province="ON", zip="k7p0e9"),
            lines=[ManualLineIn(item_code="CF0092", quantity=Decimal(1), unit="CASE")]))
        await u.commit()
    async with edi.env.factory(OP) as u:
        o = await ManualOrderService(u).submit(o.id, o.version)
        await u.commit()
    edi.clock.now = o.paid_at + timedelta(minutes=1)
    r = await edi.out.queue_due()
    assert (r.queued, r.invalid) == (1, 0)                     # no ship_to error: the 940 is valid
    assert (await edi.worker.run_once()).done == 1
    [(_, content)] = edi.store.files.items()
    segs = segments(content)
    assert ["N1", "ST", "Staff shop"] == [s for s in segs if s[0] == "N1" and s[1] == "ST"][0][:3]
    assert ["N4", "Kingston", "ON", "K7P 0E9"] == [s for s in segs if s[0] == "N4"][0][:4]
