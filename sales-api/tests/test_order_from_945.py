"""An order from a 945 with no order (FR-OB-25c, AC-OB-38): partner and PO from
the reference, one line per item, the shipment linked; a shadow order that nothing
can start — no 940, no Shopify write, no invoice, no email, no task — also after
the switch-off; shipped_ea never written; an item outside the Item Master kept; a
shipment made once; a later part joining the same order; refused outside shadow."""
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.deps import get_uow_factory
from app.domain.edi945 import zoho_reference
from app.domain.order_rules import facts_of, qualifies
from app.domain.orders import FROM_945
from app.domain.status import Fulfillment as F
from app.main import app
from app.models.config import Partner
from app.models.orders import Order
from app.models.shipments import Shipment
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError, InvalidInput
from app.runtime import build_runtime, get_runtime
from app.scripts.orders_from_945 import run
from app.services.order_from_945 import OrderFrom945
from app.services.settings import SETTINGS, SHADOW_MODE
from tests.conftest import make_token
from tests.test_invoicing import confirm, inv_status, p6  # noqa: F401 — p6 is a fixture
from tests.test_orders import OP, T0, env, po, run_850  # noqa: F401 — env is a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_outbound import send_now
from tests.test_shadow_shipments import counts, detail, shadow_on
from tests.test_shipments import p5, store_945, writes, x945  # noqa: F401 — p5 is a fixture

REF = "N9*E1*CRM-940_001008561_Walmart_4550827924"
HEADER = ["N1*ST*STORE*94*0681131037136", "N3*1 MAIN ST", "N4*Mississauga*ON*L5T 2L3*CA", "G62*11*20261104", REF, "W27*LT*WIFS"]
# One item over three pallets: W12-02 repeats the whole ordered quantity on each.
THREE_PALLETS = ["LX*1", "W12*CC*1320*540*0*EA**VN*CF0092", "LX*2", "W12*CC*1320*480*0*EA**VN*CF0092",
                 "LX*3", "W12*CC*1320*300*0*EA**VN*CF0092", "LX*4", "W12*CC*600*600*0*EA**VN*CF0010"]
UNKNOWN = ["LX*1", "W12*CC*10*10*0*EA**VN*NOPE01"]


async def a_945(t, key="001008561", rows=None, header=None, name=None, ctl="000000777") -> int:
    """A shadow-run 945 with no order: its shipment's id."""
    await shadow_on(t)
    await store_945(t, x945(key, rows or THREE_PALLETS, HEADER if header is None else header, ctl=ctl), name or f"{key}.edi")
    await t.ships.process_945s()
    return (await t.q("SELECT max(id) n FROM sales_shipments"))[0]["n"]


async def make(t, sid, partner=None, actor=OP):
    async with t.factory(actor) as u:
        o = await OrderFrom945(u).create(sid, partner)
        await u.commit()
        return o


async def lines(t, oid):
    return [(x["line_no"], x["item_code"], x["qty_ea"]) for x in await t.q(
        "SELECT line_no, item_code, qty_ea FROM sales_order_lines WHERE order_id = :o ORDER BY line_no", o=oid)]


def test_the_reference_gives_the_partner_and_the_po():
    r = zoho_reference("CRM-940_001008597_Sobeys_4630564306")
    assert (r.warehouse_order, r.partner_name, r.po) == ("001008597", "Sobeys", "4630564306")
    assert zoho_reference("CRM-940_1_Two_Words_Inc_77").partner_name == "Two_Words_Inc"
    assert zoho_reference("SHP-9497") is None and zoho_reference(None) is None


async def test_the_order_is_made_from_the_945_and_linked_and_audited(p5):  # noqa: F811
    sid = await a_945(p5)
    before = await counts(p5)
    o = await make(p5, sid)
    assert (o.partner_code, o.source_type, o.source_order_number, o.source_reference) == ("WMT", FROM_945, "4550827924", "WMT-4550827924")
    assert (o.fulfillment_status, o.invoice_status, o.shadow, o.warehouse_order_number_received) == (
        "Pending", "Not Required", True, "001008561")
    assert str(o.order_date) == "2026-11-04" and o.ship_to["city"] == "Mississauga" and o.ship_to["gln"] == "0681131037136"
    assert await lines(p5, o.id) == [(1, "CF0092", 1320), (2, "CF0010", 600)]  # W12-02 not summed: 1320, not 3960
    rows = await p5.q("SELECT order_line_id FROM sales_shipment_rows WHERE shipment_id = :s", s=sid)
    assert len(rows) == 4 and all(r["order_line_id"] for r in rows)
    [sh] = await p5.q("SELECT order_id, applied, shadow FROM sales_shipments WHERE id = :s", s=sid)
    assert (sh["order_id"], sh["applied"], sh["shadow"]) == (o.id, False, True)
    assert await p5.q("SELECT 1 FROM sales_document_links WHERE entity_type = 'order' AND entity_id = :o", o=o.id)
    [a] = await p5.q("SELECT user_name FROM sales_audit WHERE entity = 'sales_orders' AND reason LIKE 'order from 945%' LIMIT 1")
    assert a["user_name"] == OP.name
    # No price was invented, nothing applied, no task
    assert await p5.q("SELECT 1 FROM sales_order_lines WHERE order_id = :o AND unit_price IS NOT NULL", o=o.id) == []
    assert await counts(p5) == before
    d = await detail(p5, o.id)  # Shipped stays derived: shown as the shadow run's
    assert [(x.shipped_ea, x.shipped_shadow_ea, x.backordered_shadow_ea) for x in d.lines] == [(None, 1320, 0), (None, 600, 0)]


async def test_a_945_without_a_reference_needs_a_person_to_choose_the_partner(p5):  # noqa: F811
    sid = await a_945(p5, key="C0031", rows=["LX*1", "W12*CC*1*1*0*EA**VN*CF0092"], header=HEADER[:4])
    async with p5.factory(OP) as u:
        [plan] = await OrderFrom945(u).without_order()
    assert plan.partner_code is None and plan.po is None and plan.refused is None
    with pytest.raises(InvalidInput) as e:
        await make(p5, sid)
    assert e.value.code == "order945.partner_required"
    o = await make(p5, sid, "MAN")
    assert (o.partner_code, o.source_order_number, o.source_reference, o.source_order_id) == ("MAN", None, "MAN-C0031", "945-C0031")


async def test_an_item_outside_the_item_master_stays_as_a_line_with_its_problem(p5):  # noqa: F811
    sid = await a_945(p5, rows=UNKNOWN)
    o = await make(p5, sid)
    [ln] = await p5.q("SELECT item_code, qty_ea, problem, source FROM sales_order_lines WHERE order_id = :o", o=o.id)
    assert (ln["item_code"], ln["qty_ea"], ln["problem"], ln["source"]["item_code"]) == (None, 10, "item not in the Item Master", "NOPE01")
    assert (await p5.q("SELECT order_line_id FROM sales_shipment_rows"))[0]["order_line_id"] is not None
    assert o.fulfillment_status == F.PENDING and await p5.q("SELECT 1 FROM tasks") == []


async def test_a_shipment_with_an_order_is_refused(p5):  # noqa: F811
    sid = await a_945(p5)
    await make(p5, sid)
    with pytest.raises(BusinessError) as e:
        await make(p5, sid)
    assert e.value.code == "order945.has_order"
    assert (await p5.q("SELECT count(*) n FROM sales_orders"))[0]["n"] == 1


async def test_a_later_part_joins_the_same_order(p5):  # noqa: F811
    a = await a_945(p5, key="001008561a", rows=["LX*1", "W12*CC*12*7*0*EA**VN*CF0092"])
    o = await make(p5, a)
    assert o.warehouse_order_number_received == "001008561"
    await a_945(p5, key="001008561b", name="b.edi", ctl="000000778", rows=[
        "LX*1", "W12*CC*12*5*0*EA**VN*CF0092", "LX*2", "W12*CC*3*3*0*EA**VN*CF0010"])
    assert (await p5.q("SELECT count(*) n FROM sales_orders"))[0]["n"] == 1
    assert [r["order_id"] for r in await p5.q("SELECT order_id FROM sales_shipments ORDER BY id")] == [o.id, o.id]
    assert await lines(p5, o.id) == [(1, "CF0092", 12), (2, "CF0010", 3)]
    assert await p5.q("SELECT 1 FROM sales_shipment_rows WHERE order_line_id IS NULL") == []
    assert await p5.q("SELECT 1 FROM sales_order_lines WHERE shipped_ea IS NOT NULL") == []


async def test_a_part_stored_before_the_order_was_made_joins_by_the_action(p5):  # noqa: F811
    a = await a_945(p5, key="001008561a", rows=["LX*1", "W12*CC*12*7*0*EA**VN*CF0092"])
    b = await a_945(p5, key="001008561b", name="b.edi", ctl="000000778", rows=["LX*1", "W12*CC*12*5*0*EA**VN*CF0092"])
    o = await make(p5, a)
    async with p5.factory(OP) as u:
        [plan] = await OrderFrom945(u).without_order()
    assert (plan.shipment_id, plan.order_id) == (b, o.id)
    assert (await make(p5, b)).id == o.id
    assert await lines(p5, o.id) == [(1, "CF0092", 12)]


async def test_the_po_of_an_order_from_another_source_is_refused_not_duplicated(p5):  # noqa: F811
    await shadow_on(p5)
    await run_850(p5.edi.env, {"PurchaseOrder": [po("SOB", "4630564306", [{"Line": 1, "UPC": "062639300012", "Quantity": 2, "UOM": "CA"}])]})
    ref = "N9*E1*CRM-940_009_Sobeys_4630564306"
    sid = await a_945(p5, key="009", header=HEADER[:4] + [ref])  # no W06 PO: it did not match by itself
    async with p5.factory(OP) as u:
        [plan] = await OrderFrom945(u).without_order()
    assert plan.refused == "order945.exists"
    with pytest.raises(BusinessError) as e:
        await make(p5, sid)
    assert e.value.code == "order945.exists"


async def test_a_late_850_for_the_po_is_linked_and_changes_nothing(p5):  # noqa: F811
    o = await make(p5, await a_945(p5))
    r = await run_850(p5.edi.env, {"PurchaseOrder": [po("WMT", "4550827924", [{"Line": 1, "UPC": "062639300012", "Quantity": 2, "UOM": "CA", "UnitPrice": "9"}])]})
    assert (await p5.q("SELECT count(*) n FROM sales_orders"))[0]["n"] == 1, r
    assert await lines(p5, o.id) == [(1, "CF0092", 1320), (2, "CF0010", 600)]
    assert (await p5.q("SELECT source_type FROM sales_orders"))[0]["source_type"] == FROM_945


async def test_outside_the_shadow_run_it_is_refused(p5):  # noqa: F811
    sid = await a_945(p5)
    async with p5.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, False)
        await u.commit()
    with pytest.raises(BusinessError) as e:
        await make(p5, sid)
    assert e.value.code == "order945.outside_shadow"
    await store_945(p5, x945("777", THREE_PALLETS, HEADER, ctl="000000779"), "applied.edi")  # an applied no-order 945
    await p5.ships.process_945s()
    sid2 = (await p5.q("SELECT max(id) n FROM sales_shipments"))[0]["n"]
    await shadow_on(p5)
    with pytest.raises(BusinessError) as e:
        await make(p5, sid2)
    assert e.value.code == "order945.outside_shadow"


async def test_it_never_gets_a_940_shopify_write_invoice_email_or_task_also_after_the_switch_off(p6):  # noqa: F811
    t = SimpleNamespace(factory=p6.factory, q=p6.edi.env.q, ships=p6.ships, edi=p6.edi, shop=p6.edi.env.shop)
    o = await make(t, await a_945(t))
    before = await counts(t)

    async def nothing_started():
        assert (await p6.edi.out.queue_due()).queued == 0
        await p6.inv.advance()
        assert await inv_status(p6, o.id) == "Not Required"
        async with t.factory(SYSTEM_ACTOR) as u:
            order = await u.session.get(Order, o.id)
            partner = (await u.session.execute(sa.select(Partner).where(Partner.code == order.partner_code))).scalar_one()
            assert not qualifies(*facts_of(order, partner), T0.replace(year=2030))[0]
        with pytest.raises(BusinessError) as e:
            await send_now(p6.edi, o.id)
        assert e.value.code.startswith("send940.shadow")
        with pytest.raises(BusinessError) as e:
            await confirm(p6, o.id)
        assert e.value.code.startswith("invoice.")
        assert await counts(t) == before and writes(t) == []
        assert await t.q("SELECT 1 FROM sales_documents WHERE direction = 'OUT'") == []
        assert p6.store.calls == [] and p6.mail.sent == []

    await nothing_started()
    async with t.factory(SYSTEM_ACTOR) as u:  # the switch-off clears nothing
        await SETTINGS.set(u.session, SHADOW_MODE, False)
        await u.commit()
    await nothing_started()
    # Even without the flag the source keeps it from a 940.
    async with t.factory(SYSTEM_ACTOR) as u:
        order = await u.session.get(Order, o.id)
        order.shadow = False
        partner = (await u.session.execute(sa.select(Partner).where(Partner.code == order.partner_code))).scalar_one()
        assert qualifies(*facts_of(order, partner), T0.replace(year=2030))[1] == "created from a 945: it has already shipped"
        await u.commit()
    assert (await p6.edi.out.queue_due()).queued == 0


async def test_a_later_part_after_the_switch_off_joins_and_applies_nothing(p5):  # noqa: F811
    o = await make(p5, await a_945(p5, key="001008561a", rows=["LX*1", "W12*CC*12*7*0*EA**VN*CF0092"]))
    before = await counts(p5)
    async with p5.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, False)
        await u.commit()
    await store_945(p5, x945("001008561b", ["LX*1", "W12*CC*12*5*0*EA**VN*CF0092"], HEADER, ctl="000000780"), "b.edi")
    r = await p5.ships.process_945s()
    assert r.failed == 0
    assert await counts(p5) == before and writes(p5) == []
    [sh] = await p5.q("SELECT order_id, applied, shadow FROM sales_shipments ORDER BY id DESC LIMIT 1")
    assert (sh["order_id"], sh["applied"], sh["shadow"]) == (o.id, False, True)
    assert (await p5.q("SELECT fulfillment_status s FROM sales_orders"))[0]["s"] == "Pending"


async def test_shadow_compare_sees_the_order_and_no_sales_only_row(p5):  # noqa: F811
    await a_945(p5)
    async with p5.factory(SYSTEM_ACTOR) as u:
        order = await OrderFrom945(u).create((await u.session.execute(sa.select(Shipment.id))).scalar_one(), "WMT")
        await u.commit()
    assert order.source_type == FROM_945
    from app.services.shadow import ShadowRun
    run_ = ShadowRun.__new__(ShadowRun)
    run_.clock = lambda: T0.replace(year=2030)
    async with p5.factory(SYSTEM_ACTOR) as u:
        assert await ShadowRun._sales_only(run_, u, set()) == 0


async def test_the_script_is_a_dry_run_by_default_and_uses_the_same_path(p5):  # noqa: F811
    await a_945(p5)
    await a_945(p5, key="C0031", rows=["LX*1", "W12*CC*1*1*0*EA**VN*CF0092"], header=HEADER[:4], name="c.edi", ctl="000000781")
    dry = await run(p5.factory, apply=False)
    assert (dry["shipments"], dry["created"], dry["refused"], dry["lines"]) == (2, 2, 0, 3)
    assert (await p5.q("SELECT count(*) n FROM sales_orders"))[0]["n"] == 0
    # The partner "Manual" here has the code MAN: the script's fallback is dev2's code
    async with p5.factory(SYSTEM_ACTOR) as u:
        (await u.session.execute(sa.select(Partner).where(Partner.code == "MAN"))).scalar_one().code = "Manual"
        await u.commit()
    done = await run(p5.factory, apply=True)
    assert (done["created"], done["refused"]) == (2, 0)
    assert [x["order_ref"] for x in done["orders"]] == ["WMT-4550827924", "MAN-C0031"]
    again = await run(p5.factory, apply=True)
    assert (again["shipments"], again["created"]) == (0, 0)
    text = str(done)
    assert "STORE" not in text and "MAIN ST" not in text and "Mississauga" not in text


GRANTS = {"op": {"sales.read", "sales.order.operate"}, "reader": {"sales.read"}}
OPERATOR, READER = ({"Authorization": f"Bearer {make_token(r)}"} for r in ("op", "reader"))


@pytest_asyncio.fixture
async def api(p5):  # noqa: F811
    env_ = p5.edi.env

    async def matrix(session, actor, key):  # noqa: ARG001
        return key in GRANTS.get(actor.role or "", set())
    env_.factory.permissions = matrix
    runtime = build_runtime(settings, env_.factory, shopify=env_.shop)
    app.dependency_overrides[get_uow_factory] = lambda: env_.factory
    app.dependency_overrides[get_runtime] = lambda: runtime
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.p5 = p5
        yield c
    app.dependency_overrides.clear()


async def test_the_endpoints_are_read_and_operate_and_documents_find_the_945(api):
    sid = await a_945(api.p5)
    [doc] = await api.p5.q("SELECT document_id d FROM sales_shipments")
    r = await api.get(f"/api/v1/orders/shipments-without-order?document_id={doc['d']}", headers=READER)
    assert r.status_code == 200
    [plan] = r.json()
    assert (plan["partner_code"], plan["po"], plan["refused"], plan["order_id"]) == ("WMT", "4550827924", None, None)
    assert [(ln["item_code"], ln["qty_ea"]) for ln in plan["lines"]] == [("CF0092", 1320), ("CF0010", 600)]
    found = (await api.get("/api/v1/documents?filter=shipments_without_order:gt:0", headers=READER)).json()
    assert [(d["id"], d["shipments_without_order"]) for d in found["items"]] == [(doc["d"], 1)]
    assert (await api.post(f"/api/v1/orders/shipments/{sid}/create-order", json={}, headers=READER)).status_code == 403
    r = await api.post(f"/api/v1/orders/shipments/{sid}/create-order", json={}, headers=OPERATOR)
    assert r.status_code == 201 and r.json()["source_type"] == FROM_945
    assert (await api.get("/api/v1/documents?filter=shipments_without_order:gt:0", headers=READER)).json()["items"] == []
    r = await api.post(f"/api/v1/orders/shipments/{sid}/create-order", json={}, headers=OPERATOR)
    assert r.status_code == 409 and r.json()["error"]["code"] == "order945.has_order"
    r = await api.get(f"/api/v1/orders/{(await api.p5.q('SELECT id FROM sales_orders'))[0]['id']}", headers=READER)
    assert r.json()["lines"][0]["shipped_ea"] is None


