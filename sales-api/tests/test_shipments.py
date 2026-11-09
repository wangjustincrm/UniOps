"""P5: 945 → shipments (FR-OB-25 … 25d, 28) and the Shopify write-back
(FR-OB-11 / 11a). The fixture is P4's (writes ON); the Shopify store is the
in-memory fake behind the real guard — a write needs the switch AND a LIVE
connection, so the store's connection is set LIVE where a write is expected."""
import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

from app.domain.status import Fulfillment as F
from app.integrations.shopify import ShopifyForbidden
from app.models.config import Connection, Partner
from app.models.documents import Document
from app.models.records import OutboxRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.outbox import Outbox, OutboxWorker
from app.services.outbound import SEND_940
from app.services.settings import SETTINGS, SHADOW_MODE
from app.services.shipments import CLOSE, FULFILL, Shipments
from tests.test_orders import T0, env, po, run_850  # noqa: F401 — env is a fixture (p4_edi needs it)
from tests.test_outbound import a_paid_order, ack997, sent_order, status, store_997
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture

FO = [{"id": 501, "status": "open", "line_items": [{"id": 601, "line_item_id": 11, "fulfillable_quantity": 2}]}]


@pytest_asyncio.fixture
async def p5(p4_edi):  # noqa: F811 — the P4 fixture, extended
    edi = p4_edi
    ships = Shipments(uow_factory=edi.env.factory, tasks=edi.env.tasks, shopify=edi.gateways.shopify, outbound=edi.out,
                      clock=lambda: edi.clock.now)
    worker = OutboxWorker(Outbox(OutboxRecord), {SEND_940: edi.out.outbox_kind(), FULFILL: ships.fulfill_kind(),
                                                 CLOSE: ships.close_kind()}, edi.env.factory)
    edi.env.shop.fulfillment_orders_by_order["1"] = [dict(fo) for fo in FO]
    return SimpleNamespace(edi=edi, ships=ships, worker=worker, shop=edi.env.shop, q=edi.env.q, factory=edi.env.factory)


def x945(key: str, rows: list[str], header: list[str] | None = None, ctl="000000777") -> bytes:
    isa = ("ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261102*1000*U*00401*"
           f"{ctl}*0*P*>")
    body = [f"W06*N*{key}*20261102*SH-1", *(header or ["N1*ST*A B", "N4*Montreal*QC*H2X 1Y4*CA",
                                                       "DTM*011*20261102", "N9*2I*1Z-TRACK"]), *rows]
    segs = [isa, "GS*SW*9055651212*CANADAROYALMILK*20261102*1000*777*X*004010", "ST*945*0001", *body,
            f"SE*{len(body) + 2}*0001", "GE*1*777", f"IEA*1*{ctl}"]
    return ("~\n".join(segs) + "~\n").encode()


PALLET_12 = ["LX*1", "MAN*GM*00077777770077782891", "W12*CP*12*10*0*EA*062639300012*VN*CF0092", "N9*LT*LOT-A",
             "DTM*036*20280519", "W12*CP*12*2*0*EA*062639300012*VN*CF0092", "N9*LT*LOT-B", "DTM*036*20280601"]


async def store_945(t, data: bytes, name="945.edi", shadow=False) -> int:
    async with t.factory(SYSTEM_ACTOR) as u:
        d = Document(direction="IN", origin="PARTNER", system="RADIANT", kind="945_EDI", remote_name=name, size=len(data),
                     sha256=hashlib.sha256(data).hexdigest(), content=data, processing_status="Pending",
                     connection_id=t.edi.conn_id, shadow=shadow)
        u.session.add(d)
        await u.session.flush()
        await u.commit()
        return d.id


async def shipped_order(t, data: bytes, name="945.edi"):
    o, d = await sent_order(t.edi)
    await store_945(t, data, name)
    t.edi.clock.now = T0 + timedelta(days=5)  # 2026-11-03: after the 945's ship date
    r = await t.ships.process_945s()
    return o, d, r


async def live_store(t):
    async with t.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, t.edi.env.conn_id)).mode = "LIVE"
        await u.commit()


async def tasks(t, type_):
    return [r["description"] for r in await t.q("SELECT description FROM tasks WHERE type = :t", t=f"sales_{type_}")]


def writes(t):
    return [c for c, _ in t.shop.calls if c in ("create_fulfillment", "update_tracking", "close_order")]


# ── shipments and lots (FR-OB-25b, AC-OB-34 … 37) ────────────────────────────

async def test_ac34_ac35_every_w12_is_a_row_with_its_lot_sscc_and_one_line_total(p5):
    o, _, r = await shipped_order(p5, x945("9301", PALLET_12))
    assert (r.shipments, r.failed) == (1, 0) and await status(p5.edi, o.id) == F.SHIPPED
    rows = await p5.q("SELECT sscc, lot, expiry::text e, qty_ordered_ea, qty_shipped_ea, order_line_id FROM sales_shipment_rows ORDER BY id")
    assert [(x["sscc"], x["lot"], x["e"], x["qty_ordered_ea"], x["qty_shipped_ea"]) for x in rows] == [
        ("00077777770077782891", "LOT-A", "2028-05-19", 12, 10), ("00077777770077782891", "LOT-B", "2028-06-01", 12, 2)]
    assert len({x["order_line_id"] for x in rows}) == 1  # both lots on the one line
    [line] = await p5.q("SELECT shipped_ea, qty_ea FROM sales_order_lines WHERE order_id = :o", o=o.id)
    assert (line["shipped_ea"], line["qty_ea"]) == (12, 12)  # 12 ordered once, 10 + 2 shipped
    [sh] = await p5.q("SELECT ship_date::text d, ship_date_source, tracking, applied FROM sales_shipments")
    assert (sh["d"], sh["ship_date_source"], sh["tracking"], sh["applied"]) == ("2026-11-02", "DTM", ["1Z-TRACK"], True)
    [d] = await p5.q("SELECT ack_status FROM sales_documents WHERE kind = '940_EDI'")
    assert d["ack_status"] == "Accepted by 945"  # no 997 had come (AC-OB-62)


async def test_ac36_applying_the_same_945_again_changes_nothing(p5):
    o, _, _ = await shipped_order(p5, x945("9301", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"]))  # LTL: no SSCC, no tracking
    async with p5.factory(SYSTEM_ACTOR) as u:
        doc = (await u.session.execute(sa.select(Document).where(Document.kind == "945_EDI"))).scalar_one()
        from app.domain.edi945 import parse_945
        [sh] = parse_945((await u.session.execute(sa.select(Document.content).where(Document.id == doc.id))).scalar_one())
        again = await p5.ships.apply(u, doc, 1, sh, SimpleNamespace(shipments=0, no_order=0, second=0))
        await u.commit()
    assert again is None
    assert await p5.q("SELECT count(*) n FROM sales_shipment_rows") == [{"n": 1}]
    assert (await p5.q("SELECT shipped_ea FROM sales_order_lines"))[0]["shipped_ea"] == 12


async def test_ac37_a_short_shipment_closes_the_fulfilment_and_raises_one_task(p5):
    o, _, _ = await shipped_order(p5, x945("9301", ["LX*1", "W12*CP*12*10*0*EA**VN*CF0092"]))
    assert await status(p5.edi, o.id) == F.SHIPPED
    [t] = await tasks(p5, "short_shipment")
    assert "CF0092: 2 EA not shipped (10 of 12)" in t and o.source_reference in t and "no second 940" in t
    assert await p5.q("SELECT count(*) n FROM sales_documents WHERE kind = '940_EDI'") == [{"n": 1}]


# ── order matching (FR-OB-28, AC-OB-38 / 78, FR-OB-25c) ───────────────────────

async def test_ac38_ac78_suffix_and_zero_padding_match_two_letters_do_not(p5):
    o, _, _ = await shipped_order(p5, x945("000009301b", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"]))
    assert await status(p5.edi, o.id) == F.SHIPPED
    assert (await p5.q("SELECT warehouse_order_number_received w FROM sales_orders WHERE id = :i", i=o.id))[0]["w"] == "000009301b"
    await store_945(p5, x945("9301ab", ["LX*1", "MAN*GM*000123", "W12*CP*1*1*0*EA**VN*CF0092"], ctl="000000778"), "x.edi")
    r = await p5.ships.process_945s()
    assert r.no_order == 1
    [sh] = await p5.q("SELECT order_id, applied FROM sales_shipments WHERE order_key_received = '9301ab'")
    assert sh == {"order_id": None, "applied": False}
    assert await p5.q("SELECT sscc FROM sales_shipment_rows WHERE sscc = '000123'")  # stored with its pallet (FR-OB-25c)
    [t] = await tasks(p5, "945_no_order")
    assert "two trailing letters" in t and "Create the order" in t


async def test_a_retail_945_matches_by_the_internal_number(p5):
    await run_850(p5.edi.env, {"PurchaseOrder": [po("WMT", "W-2", [{"Line": 1, "BuyerPart": "W-999", "Quantity": 10, "UOM": "CA"}],
                                                     ship_to_gln="0012345000011")]})
    o, _ = await p5.edi.env.order("W-2")
    async with p5.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(type(o), o.id)).fulfillment_status = F.ACCEPTED_940.value
        await u.commit()
    await store_945(p5, x945(f"{o.order_number:09d}", ["LX*1", "W12*CP*60*58*0*EA**VN*CF0092", "W12*CP*60*2*0*EA**VN*CF0092"],
                             header=["DTM*011*20261102"]))
    await p5.ships.process_945s()
    assert await status(p5.edi, o.id) == F.SHIPPED
    assert (await p5.q("SELECT shipped_ea FROM sales_order_lines WHERE order_id = :o", o=o.id))[0]["shipped_ea"] == 60


# ── validation checks (FR-OB-25d, AC-OB-56 … 61) ─────────────────────────────

async def checks(t):
    [sh] = await t.q("SELECT checks FROM sales_shipments ORDER BY id DESC LIMIT 1")
    return {c["check"]: c for c in sh["checks"] or []}


async def test_ac56_ac57_over_shipment_notifies_and_an_extra_item_goes_to_review(p5):
    o, _, _ = await shipped_order(p5, x945("9301", ["LX*1", "W12*CP*12*14*0*EA**VN*CF0092", "LX*2", "W12*CP*1*1*0*EA**VN*CF0010"]))
    c = await checks(p5)
    assert (c["over-shipment"]["found"], c["over-shipment"]["expected"]) == (14, 12)
    assert c["extra or substituted item"]["found"] == "CF0010"
    assert await status(p5.edi, o.id) == F.SHIPPED  # not put in review by the check
    assert await p5.q("SELECT item_code FROM sales_shipment_rows WHERE needs_review") == [{"item_code": "CF0010"}]
    [t] = await tasks(p5, "945_line")
    assert "CF0010" in t
    log = [x["message"] for x in await p5.q("SELECT message FROM sales_log WHERE step = '945 validation'")]
    assert any("over-shipment" in m for m in log) and any("extra or substituted item" in m for m in log)
    [v] = await tasks(p5, "945_validation")  # P8: one notification per shipment, every check in it
    assert "over-shipment" in v and "extra or substituted item" in v and "only notify" in v


async def test_ac58_expiry_with_and_without_a_partner_minimum(p5):
    rows = ["LX*1", "W12*CP*12*6*0*EA**VN*CF0092", "N9*LT*OLD", "DTM*036*20261101",
            "W12*CP*12*6*0*EA**VN*CF0092", "N9*LT*SHORT", "DTM*036*20261201"]
    await shipped_order(p5, x945("9301", rows))
    c = await checks(p5)
    assert "lot OLD of CF0092 expired" in c["expiry"]["message"]
    assert sum(1 for x in (await p5.q("SELECT checks FROM sales_shipments"))[0]["checks"] if x["check"] == "expiry") == 1
    async with p5.factory(SYSTEM_ACTOR) as u:  # with a minimum, the short-dated lot notifies too
        (await u.session.execute(sa.select(Partner).where(Partner.code == "SHOP"))).scalar_one().min_shelf_life_days = 90
        await u.commit()
    p5.edi.env.shop.orders = []
    await a_paid_order(p5.edi, number=9302)
    await p5.edi.out.queue_due()
    await p5.edi.worker.run_once()
    await store_945(p5, x945("9302", rows, ctl="000000779"), "b.edi")
    await p5.ships.process_945s()
    [sh] = await p5.q("SELECT checks FROM sales_shipments ORDER BY id DESC LIMIT 1")
    msgs = [x["message"] for x in sh["checks"] if x["check"] == "expiry"]
    assert len(msgs) == 2 and any("the partner wants 90" in m for m in msgs)


async def test_ac59_ac60_ac61_order_state_ship_to_and_ship_date(p5):
    header = ["N1*ST*Someone Else", "N4*Laval*QC*H7N 1A1*CA", "DTM*011*20261231"]
    await shipped_order(p5, x945("9301", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"], header=header))
    c = await checks(p5)
    assert c["order state"]["message"] == "no 997 has accepted the 940 yet"          # AC-OB-59
    assert c["mismatch with the 940"]["found"] == "Someone Else H7N 1A1"             # AC-OB-60
    assert "H2X 1Y4" in c["mismatch with the 940"]["expected"]
    assert "in the future" in c["ship date"]["message"]                              # AC-OB-61
    p5.edi.env.shop.orders = []
    o2 = await a_paid_order(p5.edi, number=9302)
    await p5.edi.out.queue_due()
    await p5.edi.worker.run_once()
    await store_945(p5, x945("9302", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"], header=["DTM*011*20261001"], ctl="000000780"), "c.edi")
    await p5.ships.process_945s()
    c = await checks(p5)
    assert "before the 940 was sent" in c["ship date"]["message"] and await status(p5.edi, o2.id) == F.SHIPPED


# ── a second 945 (AC-OB-73) ──────────────────────────────────────────────────

async def test_ac73_a_second_different_945_is_stored_not_applied_and_holds_the_order(p5):
    o, _, _ = await shipped_order(p5, x945("9301", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"]), "first.edi")
    await store_945(p5, x945("9301", ["LX*1", "W12*CP*12*6*0*EA**VN*CF0092"], ctl="000000781"), "second.edi")
    r = await p5.ships.process_945s()
    assert r.second == 1 and await status(p5.edi, o.id) == F.ON_HOLD
    assert [x["applied"] for x in await p5.q("SELECT applied FROM sales_shipments ORDER BY id")] == [True, False]
    assert (await p5.q("SELECT shipped_ea FROM sales_order_lines"))[0]["shipped_ea"] == 12  # unchanged
    [t] = await tasks(p5, "second_945")
    assert "first.edi" in t and "second.edi" in t


# ── Shopify write-back (FR-OB-11 / 11a, AC-OB-39 / 72) ───────────────────────

async def test_ac39_a_997_writes_nothing_the_945_fulfils_with_tracking_then_archives(p5):
    await live_store(p5)
    o, d = await sent_order(p5.edi)
    await store_997(p5.edi, ack997(d["st02"]))
    await p5.edi.out.process_997s()
    assert writes(p5) == []  # nothing at 997 acceptance
    await store_945(p5, x945("9301", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"]))
    await p5.ships.process_945s()
    assert (await p5.worker.run_once()).done == 2  # the fulfillment, then the archive it queues
    [(_, call)] = [c for c in p5.shop.calls if c[0] == "create_fulfillment"]
    assert call["items"] == [{"fulfillment_order_id": 501, "fulfillment_order_line_items": [{"id": 601, "quantity": 2}]}]
    assert call["tracking"] == {"number": "1Z-TRACK", "company": "Purolator"} and call["notify"] is True
    assert p5.shop.closed == ["1"]
    [sh] = await p5.q("SELECT shopify_status, shopify_fulfillment_id FROM sales_shipments")
    assert sh["shopify_status"] == "Done" and sh["shopify_fulfillment_id"]
    assert writes(p5) == ["create_fulfillment", "close_order"]


async def test_ac72_a_zoho_fulfillment_gets_the_tracking_and_no_second_fulfillment(p5):
    await live_store(p5)
    p5.shop.fulfillments_by_order["1"] = [{"id": 7001, "status": "success", "tracking_number": None}]
    await shipped_order(p5, x945("9301", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"]))
    await p5.worker.run_once()
    assert writes(p5) == ["update_tracking", "close_order"]  # no create_fulfillment
    assert p5.shop.fulfillments_by_order["1"][0]["tracking_number"] == "1Z-TRACK"


async def test_archiving_without_the_scope_fails_once_with_a_task_and_a_retry_never_duplicates(p5):
    await live_store(p5)
    p5.shop.fail["close_order"] = ShopifyForbidden("the app has no permission for orders/1/close.json (HTTP 403): "
                                                   "requires write_orders")
    await shipped_order(p5, x945("9301", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"]))
    r = await p5.worker.run_once()
    assert (r.done, r.failed) == (1, 1)
    assert (await p5.worker.run_once()).done == 0  # not retried by itself
    [t] = await tasks(p5, "shopify_write")
    assert "write_orders" in t and "not retried automatically" in t
    [sh] = await p5.q("SELECT id, shopify_status FROM sales_shipments")
    assert sh["shopify_status"] == "Failed"
    del p5.shop.fail["close_order"]
    async with p5.factory(SYSTEM_ACTOR) as u:
        await p5.ships.retry_shopify(u, sh["id"])
        await u.commit()
    await p5.worker.run_once()
    assert writes(p5).count("create_fulfillment") == 1 and p5.shop.closed == ["1"]
    assert [x["is_completed"] for x in await p5.q("SELECT is_completed FROM tasks WHERE type = 'sales_shopify_write'")] == [True]


async def test_a_retried_fulfillment_reads_first_and_creates_nothing_twice(p5):
    await live_store(p5)
    p5.shop.fail["create_fulfillment"] = RuntimeError("connection reset after the store had created it")
    await shipped_order(p5, x945("9301", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"]))
    assert (await p5.worker.run_once()).failed == 1
    del p5.shop.fail["create_fulfillment"]
    p5.shop.fulfillments_by_order["1"] = [{"id": 8001, "status": "success", "tracking_number": "1Z-TRACK"}]  # it did happen
    [sh] = await p5.q("SELECT id FROM sales_shipments")
    async with p5.factory(SYSTEM_ACTOR) as u:
        await p5.ships.retry_shopify(u, sh["id"])
        await u.commit()
    await p5.worker.run_once()
    assert writes(p5).count("create_fulfillment") == 1  # only the first attempt; the retry found it


async def test_a_test_mode_store_is_never_written(p5):  # a Test-mode store: skipped, not a failure
    await shipped_order(p5, x945("9301", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"]))
    await p5.worker.run_once()
    assert writes(p5) == []
    assert (await p5.q("SELECT shopify_status FROM sales_shipments"))[0]["shopify_status"] == "Not needed"


# ── the shadow run ───────────────────────────────────────────────────────────

async def test_shadow_945s_are_stored_compared_never_applied_never_written(p5):
    await live_store(p5)                                   # writes ON, LIVE: a write would happen
    o, _ = await sent_order(p5.edi)
    async with p5.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await u.commit()
    await store_945(p5, x945("9301", PALLET_12))
    r = await p5.ships.process_945s()
    assert (r.shadow, r.shipments) == (1, 0)
    assert await status(p5.edi, o.id) == F.SENT_940                      # not applied
    [sh] = await p5.q("SELECT shadow, applied, order_id FROM sales_shipments")
    assert sh == {"shadow": True, "applied": False, "order_id": o.id}  # kept with its rows, never counted
    assert (await p5.q("SELECT shipped_ea FROM sales_order_lines"))[0]["shipped_ea"] is None
    assert await p5.q("SELECT count(*) n FROM sales_shipment_rows") == [{"n": 2}]
    assert await p5.q("SELECT 1 FROM sales_outbox WHERE kind IN ('shopify_fulfill', 'shopify_close')") == []
    assert writes(p5) == [] and await p5.q("SELECT 1 FROM tasks") == []
    async with p5.factory(SYSTEM_ACTOR) as u:              # switched off: a shadow shipment still never writes
        await SETTINGS.set(u.session, SHADOW_MODE, False)
        await u.commit()
    [s1] = await p5.q("SELECT id FROM sales_shipments")
    from app.platform.errors import BusinessError
    async with p5.factory(SYSTEM_ACTOR) as u:
        sh = await u.session.get(__import__("app.models.shipments", fromlist=["Shipment"]).Shipment, s1["id"])
        sh.shopify_status = "Failed"
        await u.commit()
    with pytest.raises(BusinessError):
        async with p5.factory(SYSTEM_ACTOR) as u:
            await p5.ships.retry_shopify(u, s1["id"])
    assert writes(p5) == []


async def test_a_write_queued_before_the_switch_on_is_cancelled(p5):
    await live_store(p5)
    await shipped_order(p5, x945("9301", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"]))  # FULFILL row queued
    async with p5.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await u.commit()
    assert (await p5.worker.run_once()).cancelled == 1 and writes(p5) == []
    assert (await p5.q("SELECT shopify_status FROM sales_shipments"))[0]["shopify_status"] == "Suppressed"


async def test_a_test_store_skips_the_shopify_write_and_the_order_goes_on_as_in_production(p5):
    """dev2 (user 2026-11-06): the only difference from production is that a Test-mode store is not
    written to — no hold, no task, the order completes; the shipment says the write was skipped."""
    o, d = await sent_order(p5.edi)
    async with p5.factory(SYSTEM_ACTOR) as u:
        conn = (await u.session.execute(sa.select(Connection).where(Connection.kind == "SHOPIFY"))).scalar_one()
        conn.mode = "TEST"
        await u.commit()
    await store_945(p5, x945(f"{o.order_number:09d}", PALLET_12[:0] + ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092", "N9*LT*L1"]),
                    "945-test-store.edi")
    await p5.ships.process_945s()
    await p5.worker.run_once()
    [sh] = await p5.q("SELECT shopify_status FROM sales_shipments")
    assert sh["shopify_status"] == "Not needed"
    assert writes(p5) == []
    assert await p5.q("SELECT type FROM tasks WHERE type LIKE 'sales_shopify%' OR type = 'sales_send_refused'") == []
    [row] = await p5.q("SELECT fulfillment_status FROM sales_orders WHERE id = :i", i=o.id)
    assert row["fulfillment_status"] == "Shipped"


@pytest.mark.parametrize("before", [F.ARCHIVE_SUPPRESSED, F.PENDING, F.REVIEW])
async def test_a_945_ships_an_order_no_940_of_ours_went_for_and_says_so(p5, before):
    """User 2026-11-09 (9567): archived in Shopify, Zoho's 940 went, the 945 came back → Shipped, not stuck."""
    from app.services.problems import ORDER_ARCHIVED, ORDER_REVIEW, problem
    o = await a_paid_order(p5.edi, number=9567)
    event = {F.ARCHIVE_SUPPRESSED: ORDER_ARCHIVED, F.REVIEW: ORDER_REVIEW}.get(before)
    async with p5.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(type(o), o.id)).fulfillment_status = before.value
        if event:
            await p5.edi.env.tasks.raise_problem(u.session, event, o.id, problem(event, "t", "d"))
        await u.commit()
    await store_945(p5, x945("9567", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"], header=["DTM*011*20261102", "N9*2I*1Z-T"]))
    p5.edi.clock.now = T0 + timedelta(days=5)
    await p5.ships.process_945s()
    assert await status(p5.edi, o.id) == F.SHIPPED
    assert (await checks(p5))["order state"]["message"] == "no 940 was sent by Sales for this order"
    if event:
        done = [r["is_completed"] for r in await p5.q("SELECT is_completed FROM tasks WHERE type = :t", t=f"sales_{event}")]
        assert done == [True]  # the goods left: the archive / review question is closed
