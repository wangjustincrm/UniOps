"""The whole order procedure, end to end, on the throwaway test database: the real
runtime (intake, connection and system jobs, outbox, guards) over fake SFTP, Shopify
and email, with sanitised files in the real shapes (Radiant's 997 with AK1 + AK9 and no
AK2, its 945 with LX pallets, N9*2I tracking after the W12 and G62*36 expiry;
TrueCommerce's 850 with Item lines and a nested ShipTo).

`see` checks every step: the internal lines, the Status, the On Hold reason, Short
shipped and the Detail, and that the SQL filter finds the order under the same values.
Run with -s to read the walk (lines starting WALK|).

A step the product does not do is an xfail with its reason, never a change to the
product: the file stays honest about what is missing.
"""
import itertools
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

import app.runtime as runtime_module
from app.domain.order_rules import can_cancel
from app.domain.status import Fulfillment as F
from app.domain.x12 import segments
from app.integrations.email import FakeEmail
from app.integrations.sftp import FakeSftp
from app.models.config import Connection, Item, ItemUnit, Partner
from app.models.orders import Order
from app.models.shipments import ShipmentRow
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.query import Filter, QuerySpec
from app.runtime import build_runtime
from app.schemas.invoices import ConfirmIn, ReleaseIn
from app.schemas.orders import ManualOrderIn
from app.services.invoice_views import InvoiceService
from app.services.order_holds import OrderActions
from app.services.order_views import ManualOrderService, OrderRepository, OrderService
from app.services.settings import EDI_CONNECTION_ID, INVOICE_CONNECTION_ID, SETTINGS, SHADOW_MODE
from app.services.shadow import mark_open_orders
from tests.test_orders import OP, env, shopify_order  # noqa: F401 — env is a fixture

FIX = Path(__file__).parent / "fixtures"
RADIANT_IN, TC_IN, TC_OUT = "/prod/out", "/tc/in", "/tc/out"
_n = itertools.count(1)


@pytest_asyncio.fixture
async def t(env, monkeypatch):  # noqa: F811
    sftp, mail = FakeSftp(), FakeEmail()
    monkeypatch.setattr(runtime_module, "AsyncsshSftp", lambda **_: sftp)
    monkeypatch.setattr(runtime_module, "HttpShopify", lambda **_: env.shop)
    monkeypatch.setattr(runtime_module, "SmtpEmail", lambda **_: mail)
    hosts = frozenset({"commhub.example", "tc.example"})
    settings = SimpleNamespace(ENVIRONMENT="production", sftp_write_hosts=hosts, sftp_allowed_hosts=hosts,
                               SALES_PARTNER_WRITES_ENABLED=True, SALES_EXTERNAL_EMAIL_ENABLED=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    env.clock.now = now
    async with env.factory(SYSTEM_ACTOR) as u:
        s = u.session
        await s.execute(sa.text("TRUNCATE sales_outbox RESTART IDENTITY"))
        for code, w in (("CF0092", "2.03"), ("CF0010", "2.48")):
            (await s.execute(sa.select(Item).where(Item.item_code == code))).scalar_one().gross_weight_lb_per_ea = Decimal(w)
        common = dict(kind="SFTP", port=22, username="crm", after_read="LEAVE", mode="LIVE", stable_seconds=0, version=1)
        radiant = Connection(name="radiant", host="commhub.example", secret_name="SALES_SECRET_RADIANT", inbound_folder=RADIANT_IN,
                             outbound_folder="/prod/in", test_outbound_folder="/test/in", **common)
        tc = Connection(name="truecommerce", host="tc.example", secret_name="SALES_SECRET_TC", inbound_folder=TC_IN,
                        outbound_folder=TC_OUT, test_outbound_folder="/tc/test", **common)
        s.add_all([radiant, tc])
        (await s.execute(sa.select(Connection).where(Connection.kind == "SHOPIFY"))).scalar_one().mode = "LIVE"
        s.add(Partner(code="HAND", name="By hand", abbrev="HND", channel="MANUAL", settlement_mode="INVOICE", sending_mode="MANUAL",
                      delay_minutes=0, invoice_method="NONE", dc_routing_required=False, version=1))
        await s.flush()
        await SETTINGS.set(s, EDI_CONNECTION_ID, radiant.id)
        await SETTINGS.set(s, INVOICE_CONNECTION_ID, tc.id)
        ids = SimpleNamespace(radiant=radiant.id, tc=tc.id)
        await u.commit()
    rt = build_runtime(settings, env.factory)
    tick = lambda: env.clock.now  # noqa: E731
    for service in (rt.outbound, rt.shipments, rt.invoicing, rt.shopify_intake):
        service.clock = tick
    async with env.factory(SYSTEM_ACTOR) as u:
        store = (await u.session.execute(sa.select(Connection.id).where(Connection.kind == "SHOPIFY"))).scalar_one()
    return SimpleNamespace(rt=rt, env=env, q=env.q, factory=env.factory, sftp=sftp, mail=mail, shop=env.shop, clock=env.clock,
                           ids=ids, store=store, settings=settings)


# ── driving it ───────────────────────────────────────────────────────────────
async def job(t, conn_id):
    return await t.rt.scheduler.connection(conn_id).tick(trigger="MANUAL", force=True)


async def system(t):
    return await t.rt.scheduler.system.tick(trigger="MANUAL", force=True)


def after(t, **delta):
    t.clock.now += timedelta(**delta)


def today(t, days=0):
    return (t.clock.now + timedelta(days=days)).astimezone(runtime_tz()).date()


def runtime_tz():
    from app.services.outbound import TORONTO
    return TORONTO


async def shopify(t, oid, number, *, lines=None, **k):
    """The store has this order as given now; the store's connection job reads it."""
    order = shopify_order(oid, number, updated=t.clock.now, lines=lines, **k)
    t.shop.orders = [o for o in t.shop.orders if o["id"] != oid] + [order]
    t.shop.fulfillment_orders_by_order[str(oid)] = [{"id": 500 + oid, "status": "open", "line_items": [
        {"id": 600 + oid, "line_item_id": 11, "fulfillable_quantity": 2}]}]
    after(t, seconds=1)
    await job(t, t.store)
    return (await order_of(t, str(oid)))


async def order_of(t, source_id):
    async with t.factory(SYSTEM_ACTOR) as u:
        return (await u.session.execute(sa.select(Order.id).where(Order.source_order_id == str(source_id)))).scalar_one()


def po850(number, *, partner="LBL", cases=(2, 1), requested=None):
    body = json.loads((FIX / "truecommerce_850_real_shape.json").read_text())
    po = body["PurchaseOrder"][0]
    po |= {"TradingPartner": partner, "PurchaseOrderNumber": number}
    if requested:
        po["RequestedDeliveryDate"] = requested.strftime("%m/%d/%Y")
    for line, qty in zip(po["Item"], cases):
        line["Quantity"] = str(qty)
    return json.dumps(body).encode()


async def retailer(t, number, **k):
    t.sftp.files[f"{TC_IN}/850-{number}.json"] = po850(number, **k)
    await job(t, t.ids.tc)
    after(t, minutes=1)  # the intake stamps real time; the test's clock runs ahead of it
    return await order_of(t, number)


async def the_940(t, oid):
    """What Radiant answers with: the group control, and the order key and PO of the W05."""
    [d] = await t.q("SELECT d.id, d.gs06, d.content FROM sales_documents d JOIN sales_document_links l ON l.document_id = d.id "
                    "WHERE l.entity_type = 'order' AND l.entity_id = :o AND d.kind = '940_EDI' ORDER BY d.id DESC LIMIT 1", o=oid)
    w05 = next(s for s in segments(bytes(d["content"])) if s[0] == "W05")
    return SimpleNamespace(gs06=d["gs06"], key=w05[2], po=w05[3] if len(w05) > 3 and w05[3] else w05[2])


def x997(group, *, accepted=True):
    n = next(_n)
    ak9 = "AK9*A*1*1*1*6" if accepted else "AK9*R*1*1*0*6"
    isa = f"ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261102*1300*U*00401*{n:09d}*0*P*>"
    return "~\n".join([isa, f"GS*FA*9055651212*CANADAROYALMILK*20261102*1300*{n}*X*004010", f"ST*997*{n}", f"AK1*OW*{group}", ak9,
                       f"SE*4*{n}", f"GE*1*{n}", f"IEA*1*{n:09d}", ""]).encode()


def x945(t, key, po, rows, *, tracking="TRK000000001"):
    """rows: (item, ordered, shipped); one LX pallet each, as Radiant writes them."""
    n = next(_n)
    day = today(t).strftime("%Y%m%d")
    body = [f"W06*F*{key}*{day}*1790{n:03d}**{po}", "N1*ST*SAMPLE RECEIVER*94*SAMPLE", "N3*1 SAMPLE STREET",
            "N4*MONTREAL*QC*H2X1Y4*CA", f"N9*MB*{n}", f"N9*BM*{n}", f"N9*PO*{po}", f"G62*11*{day}", "W27*U*PRLA**PP"]
    for i, (item, ordered, shipped) in enumerate(rows, start=1):
        sscc = f"000777777700000{n:03d}{i:02d}"
        body += [f"LX*{i}", f"MAN*GM*{sscc}", "PAL*****10.21*LB", f"W12*CC*{ordered}*{shipped}*0*EA**VN*{item}", "G69*SAMPLE ITEM",
                 f"N9*LT*LOT-{n}", *([f"N9*2I*{tracking}"] if tracking else []), f"N9*PKG*{sscc}", "G62*36*20280908"]
    body.append(f"W03*{sum(r[2] for r in rows)}*10.21*LB")
    isa = f"ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261102*1100*U*00401*{n:09d}*0*P*>"
    return "~\n".join([isa, f"GS*SW*9055651212*CANADAROYALMILK*20261102*1100*{n}*X*004010", f"ST*945*{n}", *body,
                       f"SE*{len(body) + 2}*{n}", f"GE*1*{n}", f"IEA*1*{n:09d}", ""]).encode()


async def radiant(t, data):
    t.sftp.files[f"{RADIANT_IN}/file-{next(_n)}.edi"] = data
    await job(t, t.ids.radiant)


async def accept(t, oid):
    await radiant(t, x997((await the_940(t, oid)).gs06))


async def ship(t, oid, rows, **k):
    f = await the_940(t, oid)
    await radiant(t, x945(t, f.key, f.po, rows, **k))


async def send_now(t, oid):
    async with t.factory(OP) as u:
        out = await OrderService(u).send_940_now(oid, t.rt.outbound)
        await u.commit()
        return out


async def confirm(t, oid, lines=None):
    async with t.factory(OP) as u:
        out = await InvoiceService(u).confirm(t.rt.invoicing, ConfirmIn(order_id=oid, confirmed_date=today(t), lines=lines or []))
        await u.commit()
        return out


async def send_invoice(t, invoice_id):
    async with t.factory(OP) as u:
        await InvoiceService(u).send(t.rt.invoicing, invoice_id)
        await u.commit()


async def tasks(t, name):
    return [r["is_completed"] for r in await t.q("SELECT is_completed FROM tasks WHERE type = :n", n=f"sales_{name}")]


def outgoing(t):
    return {"sftp": [c for c, _ in t.sftp.calls if c == "put"], "mail": len(t.mail.sent),
            "shopify": [c for c, _ in t.shop.calls if c in ("create_fulfillment", "update_tracking", "close_order")]}


async def see(t, oid, step, fulfillment, invoice, status, *, reason=None, short=False, detail=None):
    """One step: every value a person or the rules read, and the SQL filter agreeing."""
    async with t.factory(OP) as u:
        svc = OrderService(u)
        r = await svc.get(oid)
        found = await svc.list(QuerySpec(page=1, page_size=200, filters=[
            Filter(field="order_status", op="eq", value=status),
            *([Filter(field="on_hold_reason", op="eq", value=reason)] if reason else [])]))
    print(f"WALK|{step}|{r.fulfillment_status}|{r.invoice_status}|{r.order_status_label}|{'short' if r.short_shipped else ''}|"
          f"{r.status_detail or ''}{' +time' if r.status_detail_at else ''}")
    assert (r.fulfillment_status, r.invoice_status) == (fulfillment, invoice), step
    assert (r.order_status, r.on_hold_reason, r.short_shipped, r.status_detail) == (status, reason, short, detail), step
    assert oid in [o.id for o in found.items], f"{step}: the SQL filter does not find it under {status}"


# ── A. Shopify, normal ───────────────────────────────────────────────────────
async def test_a_shopify_normal(t):
    o = await shopify(t, 1, 9301, financial="pending")
    await see(t, o, "A1 received, unpaid", "Payment Hold", "Not Required", "On Hold", reason="Payment Required")
    o = await shopify(t, 1, 9301, financial="paid")
    await see(t, o, "A2 paid", "Pending", "Not Required", "Waiting to Send", detail="Sends at")
    await system(t)
    await see(t, o, "A3 cycle, delay running", "Pending", "Not Required", "Waiting to Send", detail="Sends at")
    assert outgoing(t)["sftp"] == []
    after(t, minutes=181)
    await system(t)
    await see(t, o, "A4 delay over, 940 sent", "940 Sent", "Not Required", "Waiting for 997", detail="Since")
    assert outgoing(t)["sftp"] == ["put"]
    await accept(t, o)
    await see(t, o, "A5 997 accepted", "940 Accepted", "Not Required", "Waiting for Packing List", detail="Since")
    await ship(t, o, [("CF0092", 12, 12)])
    await see(t, o, "A6 945 read, write queued", "Shipped", "Not Required", "Waiting for Packing List",
              detail="Writing the tracking to Shopify")
    await system(t)
    await see(t, o, "A7 fulfilled and closed", "Shipped", "Not Required", "Completed")
    assert outgoing(t)["shopify"] == ["create_fulfillment", "close_order"]
    assert next(c for n, c in t.shop.calls if n == "create_fulfillment")["tracking"]["number"] == "TRK000000001"


# ── B / C. Retailer ──────────────────────────────────────────────────────────
async def retailer_to_shipped(t, number, tag, *, partner="LBL", rows=(("CF0092", 12, 12), ("CF0010", 12, 12)), short=False):
    o = await retailer(t, number, partner=partner, requested=today(t, 3))
    await see(t, o, f"{tag}1 850 read", "Pending", "Pending", "Waiting to Send", detail="Sends at")
    await system(t)
    await see(t, o, f"{tag}2 940 sent", "940 Sent", "Pending", "Waiting for 997", detail="Since")
    await accept(t, o)
    await see(t, o, f"{tag}3 997 accepted", "940 Accepted", "Pending", "Waiting for Packing List", detail="Since")
    await ship(t, o, list(rows), tracking=None)
    await see(t, o, f"{tag}4 945 read", "Shipped", "Pending", "Waiting for Delivery", short=short)
    await system(t)
    await see(t, o, f"{tag}5 before the requested date", "Shipped", "Awaiting Requested Date", "Waiting for Delivery",
              short=short, detail="Requested date")
    after(t, days=4)
    await system(t)
    await see(t, o, f"{tag}6 requested date reached", "Shipped", "Waiting for Delivery Confirmation", "Waiting for Delivery", short=short)
    return o


async def test_b_retailer_810(t):
    o = await retailer_to_shipped(t, "4800000011", "B")
    inv = await confirm(t, o)
    await see(t, o, "B7 delivery confirmed", "Shipped", "Ready", "Waiting for Invoice")
    await send_invoice(t, inv.id)
    await see(t, o, "B8 invoice queued", "Shipped", "Queued", "Waiting for Invoice")
    await system(t)
    await see(t, o, "B9 810 sent", "Shipped", "Sent", "Completed")
    [path] = [p for p in t.sftp.files if p.startswith(TC_OUT)]
    assert json.loads(t.sftp.files[path])  # a JSON file went to TrueCommerce's folder


async def test_c_retailer_invoice_by_email(t):
    async with t.factory(SYSTEM_ACTOR) as u:
        p = (await u.session.execute(sa.select(Partner).where(Partner.code == "SOB"))).scalar_one()
        p.invoice_method, p.invoice_email, p.invoice_cc = "EMAIL", "ap@retailer.example", "ar@crm.example"
        await u.commit()
    o = await retailer_to_shipped(t, "4800000012", "C", partner="SOB")
    inv = await confirm(t, o)
    await see(t, o, "C7 delivery confirmed", "Shipped", "Ready", "Waiting for Invoice")
    await send_invoice(t, inv.id)
    await system(t)
    await see(t, o, "C8 email sent", "Shipped", "Sent", "Completed")
    [m] = t.mail.sent
    assert m.to == ("ap@retailer.example",) and m.cc == ("ar@crm.example",)
    assert len(m.attachments) == 3 and all(d.startswith(b"%PDF") for _, d, _ in m.attachments)


# ── D. Short shipment ────────────────────────────────────────────────────────
async def test_d_short_shopify(t):
    o = await shopify(t, 2, 9302)
    after(t, minutes=181)
    await system(t)
    await accept(t, o)
    await ship(t, o, [("CF0092", 12, 6)])
    await see(t, o, "D1 short 945", "Shipped", "Not Required", "Waiting for Packing List", short=True,
              detail="Writing the tracking to Shopify")
    await system(t)
    await see(t, o, "D2 Shopify written", "Shipped", "Not Required", "Completed", short=True)
    assert await tasks(t, "short_shipment") == [False]
    assert next(c for n, c in t.shop.calls if n == "create_fulfillment")["items"][0]["fulfillment_order_line_items"][0]["quantity"] == 1


async def test_d_short_retailer_invoices_the_confirmed_quantity(t):
    o = await retailer_to_shipped(t, "4800000013", "D", rows=(("CF0092", 12, 6), ("CF0010", 12, 12)), short=True)
    inv = await confirm(t, o)
    await see(t, o, "D7 delivery confirmed", "Shipped", "Ready", "Waiting for Invoice", short=True)
    rows = await t.q("SELECT item_code, qty, uom, amount FROM sales_invoice_lines WHERE invoice_id = :i ORDER BY line_no", i=inv.id)
    assert [(r["item_code"], r["qty"]) for r in rows] == [("CF0092", 1), ("CF0010", 1)]  # 6 EA = 1 case, not the 2 ordered
    await send_invoice(t, inv.id)
    await system(t)
    await see(t, o, "D8 810 sent", "Shipped", "Sent", "Completed", short=True)


# ── E. Manual ────────────────────────────────────────────────────────────────
def manual_body(partner):
    return ManualOrderIn(partner_code=partner, customer_reference="phone-1", ship_to={
        "name": "Sample Person", "phone": "514-555-0100", "address1": "1 Sample St", "city": "Montreal", "province": "QC",
        "zip": "H2X1Y4"}, lines=[{"item_code": "CF0092", "quantity": 2, "unit": "CASE"}])


async def manual(t, partner):
    async with t.factory(OP) as u:
        o = await ManualOrderService(u).create(manual_body(partner))
        await u.commit()
        return o


async def submit(t, o):
    async with t.factory(OP) as u:
        await ManualOrderService(u).submit(o.id, o.version)
        await u.commit()
    after(t, minutes=1)  # submitting stamps real time; the test's clock runs ahead of it


async def test_e_manual_automatic_partner(t):
    o = await manual(t, "MAN")
    await see(t, o.id, "E1 draft", "Draft", "Not Required", "Draft")
    await system(t)
    await see(t, o.id, "E2 a draft gets no 940", "Draft", "Not Required", "Draft")
    await submit(t, o)
    await see(t, o.id, "E3 submitted", "Pending", "Not Required", "Waiting to Send", detail="Sends at")
    await system(t)
    await see(t, o.id, "E4 940 sent", "940 Sent", "Not Required", "Waiting for 997", detail="Since")
    await accept(t, o.id)
    await ship(t, o.id, [("CF0092", 12, 12)])
    await see(t, o.id, "E5 945 read", "Shipped", "Not Required", "Completed")


async def test_e_manual_mode_partner_needs_send_940_now(t):
    o = await manual(t, "HAND")
    await submit(t, o)
    await see(t, o.id, "E6 submitted", "Pending", "Not Required", "Waiting to Send", detail="This partner sends by hand")
    await system(t)
    await see(t, o.id, "E7 the cycle sends nothing", "Pending", "Not Required", "Waiting to Send", detail="This partner sends by hand")
    await send_now(t, o.id)
    await see(t, o.id, "E8 Send 940 now", "940 Generated", "Not Required", "Waiting to Send")
    await system(t)
    await see(t, o.id, "E9 sent", "940 Sent", "Not Required", "Waiting for 997", detail="Since")


# ── F. Holds ─────────────────────────────────────────────────────────────────
BAD_LINE = [{"id": 11, "sku": "NOPE", "title": "Formula", "variant_title": "Case", "quantity": 2, "price": "50", "requires_shipping": True}]


async def sent(t, oid=3, number=9303):
    o = await shopify(t, oid, number)
    after(t, minutes=181)
    await system(t)
    return o


async def test_f_needs_review_and_recheck(t):
    o = await shopify(t, 4, 9304, lines=BAD_LINE)
    await see(t, o, "F1 unknown item", "Review", "Not Required", "On Hold", reason="Needs Review",
              detail="line 11: item not in the Item Master (sku=NOPE, title=Formula, variant_title=Case; 2 ?)")
    assert await tasks(t, "order_review") == [False]
    async with t.factory(SYSTEM_ACTOR) as u:
        u.session.add(Item(item_code="NEW1", sku="NOPE", warehouse_held=True, version=1))
        await u.session.flush()
        u.session.add(ItemUnit(item_code="NEW1", unit="EA", factor_ea=1, version=1))
        u.session.add(ItemUnit(item_code="NEW1", unit="CASE", factor_ea=6, version=1))
        await u.commit()
    async with t.factory(OP) as u:
        await OrderService(u).resolve_review(o, t.rt.tasks, t.shop)
        await u.commit()
    await see(t, o, "F2 Re-check clears it", "Pending", "Not Required", "Waiting to Send", detail="Sends at")
    assert await tasks(t, "order_review") == [True]


async def test_f_archived_in_shopify_and_unarchived(t):
    o = await shopify(t, 5, 9305, closed_at=t.clock.now.isoformat())
    await see(t, o, "F3 archived before the 940", "Archive Suppressed", "Not Required", "On Hold", reason="Archived in Shopify", detail="Archived")
    assert await tasks(t, "order_archived") == [False]
    o = await shopify(t, 5, 9305)
    await see(t, o, "F4 un-archived", "Pending", "Not Required", "Waiting to Send", detail="Sends at")
    assert await tasks(t, "order_archived") == [True]


async def test_f_940_rejected_and_resent(t):
    o = await sent(t)
    await radiant(t, x997((await the_940(t, o)).gs06, accepted=False))
    r = (await t.q("SELECT ack_detail FROM sales_documents WHERE kind = '940_EDI'"))[0]["ack_detail"]
    await see(t, o, "F5 997 rejected", "940 Rejected", "Not Required", "On Hold", reason="940 Rejected", detail=r)
    await send_now(t, o)
    await system(t)
    await see(t, o, "F6 resent", "940 Sent", "Not Required", "Waiting for 997", detail="Since")


async def test_f_940_failed_and_resent(t):
    o = await shopify(t, 3, 9303)
    after(t, minutes=181)
    t.sftp.fail["put"] = OSError("connection lost")
    await system(t)
    await see(t, o, "F7 put failed", "940 Failed", "Not Required", "On Hold", reason="940 Failed", detail="connection lost")
    t.sftp.fail.clear()
    await send_now(t, o)
    await system(t)
    await see(t, o, "F8 resent", "940 Sent", "Not Required", "Waiting for 997", detail="Since")


async def test_f_997_timeout_then_a_late_997(t):
    o = await sent(t)
    after(t, minutes=121)
    await system(t)
    await see(t, o, "F9 no 997 in time", "940 Timeout", "Not Required", "On Hold", reason="997 Timeout", detail="No 997 since")
    await accept(t, o)
    await see(t, o, "F10 late 997", "940 Accepted", "Not Required", "Waiting for Packing List", detail="Since")
    assert await tasks(t, "940_timeout") == [True]


async def test_f_997_timeout_then_a_late_945(t):
    o = await sent(t)
    after(t, minutes=121)
    await system(t)
    await ship(t, o, [("CF0092", 12, 12)])
    await see(t, o, "F11 late 945", "Shipped", "Not Required", "Waiting for Packing List", detail="Writing the tracking to Shopify")
    await system(t)
    await see(t, o, "F12 written", "Shipped", "Not Required", "Completed")


async def test_f_cancelled_at_source_before_the_940_is_silent(t):
    o = await shopify(t, 6, 9306)
    o = await shopify(t, 6, 9306, cancelled_at=t.clock.now.isoformat())
    await see(t, o, "F13 cancelled before the 940", "Cancelled", "Not Required", "Cancelled")
    assert await t.q("SELECT 1 FROM tasks WHERE type LIKE 'sales_cancel%'") == []


async def cancelled_after(t):
    o = await sent(t)
    await shopify(t, 3, 9303, cancelled_at=t.clock.now.isoformat())
    return o


async def test_f_cancelled_at_source_after_the_940_holds(t):
    o = await cancelled_after(t)
    await see(t, o, "F14 cancelled after the 940", "On Hold", "Not Required", "On Hold", reason="Cancelled at Source", detail="Cancelled")
    assert await tasks(t, "cancel_after_940") == [False]


async def conflict(t):
    o = await sent(t)
    await accept(t, o)
    await ship(t, o, [("CF0092", 12, 12)])
    await system(t)
    await ship(t, o, [("CF0092", 12, 6)])
    return o


async def test_f_945_conflict(t):
    o = await conflict(t)
    await see(t, o, "F15 second, different 945", "On Hold", "Not Required", "On Hold", reason="945 Conflict")
    assert await tasks(t, "second_945") == [False]


async def test_f_shopify_update_failed_and_retry(t):
    o = await sent(t)
    await accept(t, o)
    t.shop.fail["create_fulfillment"] = RuntimeError("the store said no")
    await ship(t, o, [("CF0092", 12, 12)])
    await system(t)
    [err] = await t.q("SELECT shopify_error FROM sales_shipments")
    await see(t, o, "F16 write failed", "Shipped", "Not Required", "On Hold", reason="Shopify Update Failed", detail=err["shopify_error"])
    t.shop.fail.clear()
    [sh] = await t.q("SELECT id FROM sales_shipments")
    async with t.factory(OP) as u:
        await OrderService(u).retry_shopify(sh["id"], t.rt.shipments)
        await u.commit()
    await see(t, o, "F17 Retry queued", "Shipped", "Not Required", "Waiting for Packing List", detail="Writing the tracking to Shopify")
    await system(t)
    await see(t, o, "F18 written", "Shipped", "Not Required", "Completed")


async def test_f_invoice_failed_and_resent(t):
    o = await retailer_to_shipped(t, "4800000014", "F")
    inv = await confirm(t, o)
    await send_invoice(t, inv.id)
    t.sftp.fail["put"] = OSError("connection lost")
    await system(t)
    [e] = await t.q("SELECT error FROM sales_invoices")
    await see(t, o, "F19 810 failed", "Shipped", "Failed", "On Hold", reason="Invoice Failed", detail=e["error"])
    t.sftp.fail.clear()
    await send_invoice(t, inv.id)
    await system(t)
    await see(t, o, "F20 resent", "Shipped", "Sent", "Completed")


async def blocked(t):
    o = await retailer(t, "4800000015", requested=today(t))
    await system(t)
    await accept(t, o)
    await ship(t, o, [("CF0092", 12, 12), ("CF0010", 12, 12), ("CRMMARK002", 1, 1)], tracking=None)  # an item not on the order
    await system(t)
    return o


async def test_f_invoice_blocked_with_its_reason(t):
    o = await blocked(t)
    await see(t, o, "F21 945 line matches no order line", "Shipped", "Blocked", "On Hold", reason="Invoice Blocked")
    assert await tasks(t, "invoice_hold") == [False]


async def extra_row(t):
    [r] = await t.q("SELECT id, shipment_id FROM sales_shipment_rows WHERE needs_review")
    return r["shipment_id"], r["id"]


async def resolve_row(t, line=None, reason=None):
    sid, rid = await extra_row(t)
    async with t.factory(OP) as u:
        await OrderActions(u, t.rt.tasks).resolve_row(sid, rid, line, reason)
        await u.commit()


async def test_f_release_waits_for_the_row_and_accepting_it_lets_the_invoice_go(t):
    o = await blocked(t)
    with pytest.raises(BusinessError) as e:
        async with t.factory(OP) as u:
            await InvoiceService(u).release(t.rt.invoicing, ReleaseIn(order_id=o, reason="extra sample, no charge"))
    assert e.value.code == "invoice.rows_unresolved"
    with pytest.raises(BusinessError) as e:
        await resolve_row(t)
    assert e.value.code == "shipment.reason_required"
    await resolve_row(t, reason="extra sample, no charge")
    await see(t, o, "F22 row accepted", "Shipped", "Pending", "Waiting for Delivery")
    assert await tasks(t, "invoice_hold") == [True]
    await system(t)
    await see(t, o, "F23 next cycle stays released", "Shipped", "Waiting for Delivery Confirmation", "Waiting for Delivery")
    assert await t.q("SELECT 1 FROM sales_audit WHERE reason LIKE '945 row CRMMARK002: accepted%'")
    inv = await confirm(t, o)
    rows = await t.q("SELECT item_code FROM sales_invoice_lines WHERE invoice_id = :i ORDER BY line_no", i=inv.id)
    assert [r["item_code"] for r in rows] == ["CF0092", "CF0010"]  # accepted: not on the invoice


async def test_f_a_row_of_another_item_cannot_be_linked_and_accepted_adds_nothing(t):
    o = await blocked(t)  # CRMMARK002 on an order of CF0092 and CF0010
    [line] = await t.q("SELECT id FROM sales_order_lines WHERE order_id = :o AND item_code = 'CF0010'", o=o)
    with pytest.raises(BusinessError) as e:
        await resolve_row(t, line["id"])
    assert e.value.code == "shipment.row_other_item"
    async with t.factory(OP) as u:
        d = await OrderService(u).detail(o)
    assert [r.matching_lines for s in d.shipments for r in s.rows if r.needs_review] == [[]]  # the form offers only Accept
    await resolve_row(t, reason="substituted by the warehouse")
    assert [r["shipped_ea"] for r in await t.q("SELECT shipped_ea FROM sales_order_lines WHERE order_id = :o ORDER BY line_no", o=o)] == [12, 12]


async def test_f_a_row_of_the_same_item_links_and_counts_on_the_line_and_the_invoice(t):
    o = await retailer(t, "4800000019", requested=today(t))
    await system(t)
    await accept(t, o)
    await ship(t, o, [("CF0092", 12, 12), ("CF0010", 12, 6), ("NEW-CODE", 6, 6)], tracking=None)  # unknown when it came
    await system(t)
    await see(t, o, "F29 unknown row", "Shipped", "Blocked", "On Hold", reason="Invoice Blocked", short=True)
    async with t.factory(SYSTEM_ACTOR) as u:  # the Item Master learns the code
        (await u.session.execute(sa.select(Item).where(Item.item_code == "CF0010"))).scalar_one().upc = "NEW-CODE"
        await u.commit()
    [line] = await t.q("SELECT id FROM sales_order_lines WHERE order_id = :o AND item_code = 'CF0010'", o=o)
    async with t.factory(OP) as u:
        d = await OrderService(u).detail(o)
    assert [r.matching_lines for s in d.shipments for r in s.rows if r.needs_review] == [[line["id"]]]
    await resolve_row(t, line["id"])
    await see(t, o, "F30 linked", "Shipped", "Pending", "Waiting for Delivery")
    assert (await t.q("SELECT shipped_ea FROM sales_order_lines WHERE id = :i", i=line["id"]))[0]["shipped_ea"] == 12
    assert await t.q("SELECT 1 FROM sales_shipment_rows WHERE needs_review") == []


async def test_f_a_part_case_holds_the_invoice_and_release_lets_it_go(t):
    o = await retailer_to_shipped(t, "4800000017", "F", rows=(("CF0092", 12, 5), ("CF0010", 12, 12)), short=True)
    inv = await confirm(t, o)
    await see(t, o, "F24 part case at confirmation", "Shipped", "Blocked", "On Hold", reason="Invoice Blocked", short=True)
    async with t.factory(OP) as u:
        await InvoiceService(u).release(t.rt.invoicing, ReleaseIn(order_id=o, reason="agreed with the buyer"))
        await u.commit()
    await see(t, o, "F25 released", "Shipped", "Ready", "Waiting for Invoice", short=True)
    await system(t)
    await see(t, o, "F26 stays released", "Shipped", "Ready", "Waiting for Invoice", short=True)
    await send_invoice(t, inv.id)
    await system(t)
    await see(t, o, "F27 sent", "Shipped", "Sent", "Completed", short=True)


async def test_f_payment_hold_cancelled_at_source_is_silent(t):
    o = await shopify(t, 10, 9310, financial="pending")
    o = await shopify(t, 10, 9310, financial="pending", cancelled_at=t.clock.now.isoformat())
    await see(t, o, "F28 unpaid, cancelled", "Cancelled", "Not Required", "Cancelled")


async def cancel(t, o, reason="agreed with the customer"):
    async with t.factory(OP) as u:
        await OrderActions(u, t.rt.tasks).cancel(o, reason)
        await u.commit()


async def release(t, o, to, reason="agreed with the warehouse"):
    async with t.factory(OP) as u:
        await OrderActions(u, t.rt.tasks).release_hold(o, to, reason)
        await u.commit()


async def exits(t, o):
    async with t.factory(OP) as u:
        d = await OrderService(u).detail(o)
    return d.cancellable, d.hold_exits


@pytest.mark.parametrize(("state", "allowed"), [
    (s.value, s in (F.REVIEW, F.PAYMENT_HOLD, F.PENDING, F.ARCHIVE_SUPPRESSED, F.GENERATED_940, F.REJECTED_940, F.TIMEOUT_940,
                    F.FAILED_940, F.ON_HOLD)) for s in F])
def test_what_can_be_cancelled(state, allowed):
    assert can_cancel(state, shadow=False) is allowed and can_cancel(state, shadow=True) is False


async def test_f_cancel_an_order_with_a_generated_940_writes_nothing(t):
    o = await shopify(t, 17, 9317)
    after(t, minutes=181)
    await t.rt.outbound.queue_due()
    await see(t, o, "F30 940 generated", "940 Generated", "Not Required", "Waiting to Send")
    await cancel(t, o)
    await see(t, o, "F31 cancelled", "Cancelled", "Not Required", "Cancelled")
    [d] = await t.q("SELECT file_status FROM sales_documents WHERE kind = '940_EDI'")
    assert d["file_status"] == "Cancelled"
    await system(t)
    assert outgoing(t) == {"sftp": [], "mail": 0, "shopify": []}
    assert await t.q("SELECT 1 FROM sales_audit WHERE reason = 'order cancelled: agreed with the customer'")


async def test_f_cancel_closes_the_order_task_and_a_sent_940_cannot_be_cancelled_directly(t):
    o = await shopify(t, 4, 9304, lines=BAD_LINE)
    await cancel(t, o)
    await see(t, o, "F32 review cancelled", "Cancelled", "Not Required", "Cancelled")
    assert await tasks(t, "order_review") == [True]
    o = await sent(t)
    with pytest.raises(BusinessError) as e:
        await cancel(t, o)
    assert e.value.code == "order.not_cancellable" and "through On Hold" in e.value.message
    assert (await exits(t, o)) == (False, [])


async def test_f_cancelled_at_source_exits(t):
    o = await cancelled_after(t)
    assert await exits(t, o) == (True, ["Cancelled", "940 Sent"])  # Shipped only once a 945 says so
    with pytest.raises(BusinessError) as e:
        await release(t, o, "Shipped")
    assert e.value.code == "order.hold_exit"
    await release(t, o, "940 Sent")
    await see(t, o, "F33 back to what it held", "940 Sent", "Not Required", "Waiting for 997", detail="Since")
    assert await tasks(t, "cancel_after_940") == [True]


async def test_f_cancelled_at_source_released_to_cancelled(t):
    o = await cancelled_after(t)
    await release(t, o, "Cancelled")
    await see(t, o, "F34 cancelled", "Cancelled", "Not Required", "Cancelled")
    assert outgoing(t)["shopify"] == []


async def test_f_a_945_after_cancelled_at_source_stays_held_until_a_person_says_shipped(t):
    o = await cancelled_after(t)
    await ship(t, o, [("CF0092", 12, 12)])
    await system(t)
    await see(t, o, "F35 945 on a cancelled order", "On Hold", "Not Required", "On Hold", reason="Cancelled at Source", detail="Cancelled")
    assert outgoing(t)["shopify"] == []
    assert (await t.q("SELECT shipped_ea FROM sales_order_lines"))[0]["shipped_ea"] == 12
    [task] = await t.q("SELECT description, is_completed FROM tasks WHERE type = 'sales_cancel_after_940'")
    assert "CF0092 12" in task["description"] and not task["is_completed"]
    assert await exits(t, o) == (True, ["Cancelled", "Shipped", "940 Sent"])
    await release(t, o, "Shipped")
    await see(t, o, "F36 a person says Shipped", "Shipped", "Not Required", "Waiting for Packing List", detail="Writing the tracking to Shopify")
    await system(t)
    await see(t, o, "F37 written", "Shipped", "Not Required", "Completed")
    assert outgoing(t)["shopify"] == ["create_fulfillment", "close_order"]


async def test_f_a_shopify_write_queued_before_the_hold_cannot_go(t):
    o = await sent(t)
    await accept(t, o)
    await ship(t, o, [("CF0092", 12, 12)])  # the write is queued …
    await shopify(t, 3, 9303, cancelled_at=t.clock.now.isoformat())  # … and the order goes On Hold before it runs
    await system(t)
    assert outgoing(t)["shopify"] == []
    await see(t, o, "F38 refused by the pre-check", "On Hold", "Not Required", "On Hold", reason="Cancelled at Source", detail="Cancelled")
    [sh] = await t.q("SELECT shopify_status FROM sales_shipments")
    assert sh["shopify_status"] == "Suppressed"


async def test_f_945_conflict_keep_the_applied_one(t):
    o = await conflict(t)
    assert await exits(t, o) == (True, ["Shipped", "Cancelled"])
    await release(t, o, "Shipped")
    await see(t, o, "F39 applied 945 kept", "Shipped", "Not Required", "Completed")
    assert (await t.q("SELECT shipped_ea FROM sales_order_lines"))[0]["shipped_ea"] == 12  # the second changed nothing (AC-OB-73)
    assert [r["applied"] for r in await t.q("SELECT applied FROM sales_shipments ORDER BY id")] == [True, False]
    assert await tasks(t, "second_945") == [True]
    assert outgoing(t)["shopify"] == ["create_fulfillment", "close_order"]  # written once, not again


async def test_f_945_conflict_cancelled(t):
    o = await conflict(t)
    await release(t, o, "Cancelled")
    await see(t, o, "F40 cancelled", "Cancelled", "Not Required", "Cancelled")


async def test_f_a_cancelled_order_is_not_invoiced(t):
    o = await blocked(t)  # Shipped, its invoice in progress: then a second 945 and Cancel
    await resolve_row(t, reason="sample")
    await system(t)
    await ship(t, o, [("CF0092", 12, 1)], tracking=None)
    await release(t, o, "Cancelled")
    with pytest.raises(BusinessError) as e:
        await confirm(t, o)
    assert e.value.code == "invoice.order_cancelled"


async def test_g_shadow_orders_have_no_actions_and_the_server_refuses(t):
    o = await shopify(t, 18, 9318)
    async with t.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await mark_open_orders(u)
        await u.commit()
    assert await exits(t, o) == (False, [])
    for act in (cancel(t, o), release(t, o, "Cancelled")):
        with pytest.raises(BusinessError) as e:
            await act
        assert e.value.code == "order.shadow"


@pytest.mark.xfail(strict=True, reason="After Sales closes a Shopify order, the next pull brings closed_at but a Shipped order returns "
                                       "before the archive step: the Archived fact, its filter and the Shopify section stay empty.")
async def test_a_the_archive_sales_made_shows_after_the_next_pull(t):
    o = await shopify(t, 11, 9311)
    after(t, minutes=181)
    await system(t)
    await accept(t, o)
    await ship(t, o, [("CF0092", 12, 12)])
    await system(t)
    await shopify(t, 11, 9311, closed_at=t.clock.now.isoformat())
    await see(t, o, "A8 next pull", "Shipped", "Not Required", "Completed")
    [r] = await t.q("SELECT archived_in_shopify_at a FROM sales_orders WHERE id = :o", o=o)
    assert r["a"] is not None


async def parcels(t, oid, number):
    o = await shopify(t, oid, number)
    after(t, minutes=181)
    await system(t)
    await accept(t, o)
    f = await the_940(t, o)
    second = b"LX*2~\nW12*CC*1*1*0*EA**VN*CRMMARK002~\nN9*2I*TRK000000002~\nN9*2I*TRK000000001~\nW03*"  # the first number again: sent once
    return o, x945(t, f.key, f.po, [("CF0092", 12, 12)]).replace(b"W03*", second)


async def test_a_every_parcels_tracking_reaches_shopify(t):
    o, data = await parcels(t, 12, 9312)
    await radiant(t, data)
    await system(t)
    [tracking] = [c["tracking"] for n, c in t.shop.calls if n == "create_fulfillment"]
    assert tracking == {"numbers": ["TRK000000001", "TRK000000002"], "company": "Purolator"}
    await see(t, o, "A9 two parcels", "Shipped", "Not Required", "Completed")


async def test_a_several_numbers_are_added_to_a_zoho_fulfillment_too(t):
    o, data = await parcels(t, 21, 9321)
    t.shop.fulfillments_by_order["21"] = [{"id": 77, "status": "success", "tracking_number": None}]
    await radiant(t, data)
    await system(t)
    [tracking] = [c["tracking"] for n, c in t.shop.calls if n == "update_tracking"]
    assert tracking["numbers"] == ["TRK000000001", "TRK000000002"]


async def test_the_open_view_is_the_first_and_finds_on_hold_but_no_closed_order(t):
    assert OrderRepository.presets[0].key == "open"
    done = await manual(t, "HAND")           # stays a Draft
    review = await shopify(t, 4, 9304, lines=BAD_LINE)
    going = await shopify(t, 22, 9322)
    gone = await shopify(t, 23, 9323, cancelled_at=t.clock.now.isoformat())
    state = OrderRepository.presets[0].as_dict()["state"]
    f = state["filters"][0]
    spec = QuerySpec(page=1, page_size=50, filters=[Filter(field=f["field"], op=f["op"], value=f["value"])], sort=[])
    async with t.factory(OP) as u:
        page = await OrderService(u).list(spec)
    assert {o.id for o in page.items} == {review, going} and page.total == 2  # the count, server-side
    assert done.id != gone


async def test_d_the_invoice_takes_the_confirmed_quantity_not_the_shipped_one(t):
    o = await retailer_to_shipped(t, "4800000018", "D")
    lines = await t.q("SELECT id, item_code FROM sales_order_lines WHERE order_id = :o ORDER BY line_no", o=o)
    inv = await confirm(t, o, [{"order_line_id": ln["id"], "delivered_qty": 6 if ln["item_code"] == "CF0092" else 12} for ln in lines])
    await see(t, o, "D9 confirmed less than shipped", "Shipped", "Ready", "Waiting for Invoice")
    rows = await t.q("SELECT item_code, qty FROM sales_invoice_lines WHERE invoice_id = :i ORDER BY line_no", i=inv.id)
    assert [(r["item_code"], r["qty"]) for r in rows] == [("CF0092", 1), ("CF0010", 1)]  # 6 of the 12 shipped


# ── One item on two lines (SHP-9533 and SHP-9451 on dev2) ────────────────────
TWO_LINES = [{"id": 11, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Trial Can", "quantity": 1, "price": "5", "requires_shipping": True},
             {"id": 12, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Case", "quantity": 1, "price": "50", "requires_shipping": True}]
TWO_EA_LINES = [{"id": 11, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Trial Can", "quantity": 1, "price": "5", "requires_shipping": True},
                {"id": 12, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Can", "quantity": 1, "price": "5", "requires_shipping": True}]


async def two_lines(t, oid, number, lines):
    o = await shopify(t, oid, number, lines=lines)
    t.shop.fulfillment_orders_by_order[str(oid)] = [{"id": 500 + oid, "status": "open", "line_items": [
        {"id": 700, "line_item_id": 11, "fulfillable_quantity": 1}, {"id": 701, "line_item_id": 12, "fulfillable_quantity": 1}]}]
    return o


async def figures(t, o):
    async with t.factory(OP) as u:
        d = await OrderService(u).detail(o)
    return [(ln.qty_ea, ln.shipped_ea, ln.backordered_ea, ln.shipped_shadow_ea, ln.backordered_shadow_ea) for ln in d.lines]


async def rows_of(t, o):
    """(row EA, its line's EA), the 945's order."""
    return [(r["q"], r["l"]) for r in await t.q(
        "SELECT r.qty_shipped_ea q, l.qty_ea l FROM sales_shipment_rows r JOIN sales_shipments s ON s.id = r.shipment_id "
        "JOIN sales_order_lines l ON l.id = r.order_line_id WHERE s.order_id = :o ORDER BY r.lx_index", o=o)]


async def test_one_item_on_two_lines_in_full_is_not_short_applied(t):
    o = await two_lines(t, 13, 9313, TWO_LINES)
    after(t, minutes=181)
    await system(t)
    await accept(t, o)
    await ship(t, o, [("CF0092", 7, 6), ("CF0092", 7, 1)])  # the 6 first: it fills line 1 and most of line 2
    await see(t, o, "H1 two lines, 7 of 7, applied", "Shipped", "Not Required", "Waiting for Packing List",
              detail="Writing the tracking to Shopify")
    assert await figures(t, o) == [(1, 1, 0, None, None), (6, 6, 0, None, None)]
    assert await rows_of(t, o) == [(6, 6), (1, 1)]  # each row under its own line
    assert await tasks(t, "short_shipment") == []
    await system(t)
    await see(t, o, "H2 written", "Shipped", "Not Required", "Completed")
    items = next(c for n, c in t.shop.calls if n == "create_fulfillment")["items"][0]["fulfillment_order_line_items"]
    assert [i["quantity"] for i in items] == [1, 1]


async def test_one_item_on_two_lines_really_short_applied(t):
    o = await two_lines(t, 14, 9314, TWO_EA_LINES)
    after(t, minutes=181)
    await system(t)
    await accept(t, o)
    await ship(t, o, [("CF0092", 2, 1)])
    await see(t, o, "H3 two lines, 1 of 2, applied", "Shipped", "Not Required", "Waiting for Packing List", short=True,
              detail="Writing the tracking to Shopify")
    assert await figures(t, o) == [(1, 1, 0, None, None), (1, 0, 1, None, None)]
    assert await tasks(t, "short_shipment") == [False]


async def shadow_945(t, o, number, rows):
    async with t.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await mark_open_orders(u)
        await u.commit()
    await radiant(t, x945(t, str(number), str(number), rows))


async def test_one_item_on_two_lines_in_full_is_not_short_shadow(t):
    o = await two_lines(t, 15, 9315, TWO_LINES)
    await shadow_945(t, o, 9315, [("CF0092", 7, 6), ("CF0092", 7, 1)])
    await see(t, o, "H4 two lines, 7 of 7, shadow", "Pending", "Not Required", "Completed (shadow)")
    assert await figures(t, o) == [(1, None, None, 1, 0), (6, None, None, 6, 0)]
    assert await rows_of(t, o) == [(6, 6), (1, 1)]


async def test_a_row_that_fits_no_line_keeps_the_totals_and_notifies(t):
    o = await two_lines(t, 19, 9319, TWO_LINES)  # lines of 1 and 6; rows of 3 and 4
    after(t, minutes=181)
    await system(t)
    await accept(t, o)
    await ship(t, o, [("CF0092", 7, 3), ("CF0092", 7, 4)])
    await see(t, o, "H6 rows 3 and 4 on lines 1 and 6", "Shipped", "Not Required", "Waiting for Packing List",
              detail="Writing the tracking to Shopify")
    assert await figures(t, o) == [(1, 1, 0, None, None), (6, 6, 0, None, None)]
    assert await rows_of(t, o) == [(3, 6), (4, 6)]  # the 3 did not fit: linked to the largest line
    assert await tasks(t, "945_validation") == [False]
    [sh] = await t.q("SELECT checks FROM sales_shipments")
    assert [c["check"] for c in sh["checks"]].count("row fits no line") == 1


async def test_relink_moves_old_rows_by_the_rule_and_a_dry_run_writes_nothing(t):
    o = await two_lines(t, 20, 9320, TWO_LINES)
    await shadow_945(t, o, 9320, [("CF0092", 7, 6), ("CF0092", 7, 1)])
    ids = [r["id"] for r in await t.q("SELECT id FROM sales_order_lines WHERE order_id = :o ORDER BY line_no", o=o)]
    rows = [r["id"] for r in await t.q("SELECT id FROM sales_shipment_rows ORDER BY lx_index")]
    async with t.factory(SYSTEM_ACTOR) as u:  # as the old matching left them: the 6 on the 1 EA line
        for row, line in zip(rows, ids):
            (await u.session.get(ShipmentRow, row)).order_line_id = line
        await u.commit()
    async with t.factory(SYSTEM_ACTOR) as u:
        dry = await t.rt.shipments.relink_shadow_rows(u, dry_run=True)
    assert (dry["shipments"], dry["rows_moved"]) == (1, 2)
    assert await rows_of(t, o) == [(6, 1), (1, 6)]
    async with t.factory(SYSTEM_ACTOR) as u:
        await t.rt.shipments.relink_shadow_rows(u, dry_run=False)
        await u.commit()
    assert await rows_of(t, o) == [(6, 6), (1, 1)]
    async with t.factory(SYSTEM_ACTOR) as u:
        assert (await t.rt.shipments.relink_shadow_rows(u, dry_run=True))["rows_moved"] == 0


async def test_one_item_on_two_lines_really_short_shadow(t):
    o = await two_lines(t, 16, 9316, TWO_EA_LINES)
    await shadow_945(t, o, 9316, [("CF0092", 2, 1)])
    await see(t, o, "H5 two lines, 1 of 2, shadow", "Pending", "Not Required", "Completed (shadow)", short=True)
    assert await figures(t, o) == [(1, None, None, 1, 0), (1, None, None, 0, 1)]


# ── G. The shadow run ────────────────────────────────────────────────────────
async def test_g_shadow_statuses_and_nothing_goes_out(t):
    open_shop = await shopify(t, 7, 9307)
    shipped_shop = await shopify(t, 8, 9308)
    retail = await retailer(t, "4800000016", requested=today(t, 3))
    async with t.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await mark_open_orders(u)
        await u.commit()
    await see(t, open_shop, "G1 open Shopify", "Pending", "Not Required", "With Zoho (shadow)")
    await see(t, retail, "G2 open retailer", "Pending", "Pending", "With Zoho (shadow)")
    await radiant(t, x945(t, "9308", "9308", [("CF0092", 12, 12)]))
    await radiant(t, x945(t, "001009000", "4800000016", [("CF0092", 12, 12), ("CF0010", 12, 12)], tracking=None))
    await see(t, shipped_shop, "G3 Shopify with a 945", "Pending", "Not Required", "Completed (shadow)")
    await see(t, retail, "G4 retailer with a 945", "Pending", "Pending", "Waiting for Delivery (shadow)")
    after(t, days=5)
    await system(t)
    await see(t, shipped_shop, "G5 after a cycle", "Pending", "Not Required", "Completed (shadow)")
    await see(t, retail, "G6 after a cycle", "Pending", "Pending", "Waiting for Delivery (shadow)")
    assert outgoing(t) == {"sftp": [], "mail": 0, "shopify": []}
    assert await t.q("SELECT 1 FROM sales_documents WHERE direction = 'OUT'") == []
    assert await t.q("SELECT 1 FROM sales_order_lines WHERE shipped_ea IS NOT NULL") == []
    with pytest.raises(BusinessError) as e:
        await send_now(t, open_shop)
    assert e.value.code.startswith("send940.shadow")


async def test_g_fulfilled_and_archived_without_a_945_is_with_zoho_until_the_945(t):
    """SHP-9544: Zoho fulfills and archives before the goods leave."""
    o = await shopify(t, 24, 9324)
    async with t.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await mark_open_orders(u)
        await u.commit()
    order = shopify_order(24, 9324, updated=t.clock.now, closed_at=t.clock.now.isoformat()) | {"fulfillment_status": "fulfilled"}
    t.shop.orders = [order]
    after(t, seconds=1)
    await job(t, t.store)
    await see(t, o, "G8 fulfilled and archived, no 945", "Archive Suppressed", "Not Required", "With Zoho (shadow)")
    state = OrderRepository.presets[0].as_dict()["state"]["filters"][0]
    async with t.factory(OP) as u:
        found = await OrderService(u).list(QuerySpec(page=1, page_size=50, filters=[Filter(field=state["field"], op=state["op"], value=state["value"])]))
    assert o in [x.id for x in found.items]  # still in Open
    await radiant(t, x945(t, "9324", "9324", [("CF0092", 12, 12)]))
    await see(t, o, "G9 the 945 came", "Archive Suppressed", "Not Required", "Completed (shadow)")


async def test_g_archived_and_not_fulfilled_still_shows_in_shadow(t):
    o = await shopify(t, 9, 9309, closed_at=t.clock.now.isoformat())
    async with t.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await mark_open_orders(u)
        await u.commit()
    await see(t, o, "G7 archived, not fulfilled", "Archive Suppressed", "Not Required", "On Hold", reason="Archived in Shopify", detail="Archived")
