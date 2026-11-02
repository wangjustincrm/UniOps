"""P6: delivery confirmation and invoices (FR-OB-19, 30 … 35; AC-OB-40 … 45).
P5's fixture (writes ON) with the guarded SFTP and email gateways around
in-memory fakes: a put lands in the fake store, an email in the fake outbox —
only when the guard lets it through."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

from app.container import build_container
from app.domain.status import Fulfillment as F
from app.integrations.email import FakeEmail
from app.models.config import Partner
from app.models.orders import Order
from app.models.records import OutboxRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.outbox import Outbox, OutboxWorker
from app.services.invoice_views import InvoiceService
from app.services.invoicing import SEND_INVOICE, Invoicing
from app.services.settings import INVOICE_CONNECTION_ID, SETTINGS, SHADOW_MODE
from app.schemas.invoices import ConfirmBulkIn, ConfirmIn, DeliveredLine, InvoiceEdit, ReleaseIn, ReturnIn
from tests.test_orders import OP, env, po, run_850  # noqa: F401 — env is a fixture (p4_edi needs it)
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_shipments import store_945, x945
from app.services.shipments import Shipments

NOV5 = datetime(2026, 11, 5, 17, 0, tzinfo=timezone.utc)  # 12:00 in Toronto, the 850's requested date


@pytest_asyncio.fixture
async def p6(p4_edi):  # noqa: F811 — the P4 fixture, extended
    edi = p4_edi
    mail, refused = FakeEmail(), []

    async def sink(r):
        refused.append(r)
    gw = build_container(edi.settings, real_sftp=lambda: edi.store, real_email=lambda: mail,
                         real_shopify=lambda: edi.env.shop, refusal_sink=sink)
    clock = SimpleNamespace(now=NOV5)
    inv = Invoicing(uow_factory=edi.env.factory, tasks=edi.env.tasks, sftp=gw.sftp, email=gw.email, clock=lambda: clock.now)
    ships = Shipments(uow_factory=edi.env.factory, tasks=edi.env.tasks, shopify=gw.shopify, outbound=edi.out,
                      clock=lambda: clock.now)
    worker = OutboxWorker(Outbox(OutboxRecord), {SEND_INVOICE: inv.outbox_kind()}, edi.env.factory)
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, INVOICE_CONNECTION_ID, edi.conn_id)  # the fake store stands in for TrueCommerce
        await u.commit()
    return SimpleNamespace(edi=edi, inv=inv, ships=ships, worker=worker, mail=mail, refused=refused, clock=clock,
                           q=edi.env.q, factory=edi.env.factory, store=edi.store)


async def shipped_850(t, number="PO-1", partner="LBL", qty=10, shipped=None, extra=None, price="50"):
    """An 850 order, 940 accepted, a 945 shipping `shipped` EA (default all)."""
    await run_850(t.edi.env, {"PurchaseOrder": [po(partner, number, [
        {"Line": 1, "BuyerPart": {"LBL": "L-555", "SOB": "S-777", "WMT": "W-999"}[partner], "Quantity": qty, "UOM": "CA",
         "UnitPrice": price}],
        ship_to_gln="0012345000011") | (extra or {})]})
    o, _ = await t.edi.env.order(number)
    async with t.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, o.id)).fulfillment_status = F.ACCEPTED_940.value
        await u.commit()
    ea = qty * 6 if shipped is None else shipped
    await store_945(t, x945(f"{o.order_number:09d}", ["LX*1", f"W12*CP*{qty * 6}*{ea}*0*EA**VN*CF0092", "N9*LT*LOT-1"],
                            header=["DTM*011*20261102"], ctl=f"{900000000 + o.id}"), f"945-{number}.edi")
    await t.ships.process_945s()
    return o


async def inv_status(t, oid):
    return (await t.q("SELECT invoice_status FROM sales_orders WHERE id = :i", i=oid))[0]["invoice_status"]


async def tasks(t, type_):
    return [r["description"] for r in await t.q("SELECT description FROM tasks WHERE type = :t", t=f"sales_{type_}")]


async def confirm(t, oid, *, when=date(2026, 11, 5), lines=None):
    async with t.factory(OP) as u:
        out = await InvoiceService(u).confirm(t.inv, ConfirmIn(order_id=oid, confirmed_date=when, lines=lines or []))
        await u.commit()
        return out


async def send(t, invoice_id):
    async with t.factory(OP) as u:
        out = await InvoiceService(u).send(t.inv, invoice_id)
        await u.commit()
        return out


# ── the worklist (FR-OB-31, AC-OB-41 / 43) ───────────────────────────────────

async def test_ac41_shipped_and_the_requested_date_reached_both_needed(p6):
    early = await shipped_850(p6, "PO-1")
    await run_850(p6.edi.env, {"PurchaseOrder": [po("LBL", "PO-2", [{"Line": 1, "BuyerPart": "L-555", "Quantity": 1, "UOM": "CA"}])]})
    unshipped, _ = await p6.edi.env.order("PO-2")
    p6.clock.now = NOV5 - timedelta(days=2)
    await p6.inv.advance()
    assert await inv_status(p6, early.id) == "Awaiting Requested Date"   # shipped, before its date
    p6.clock.now = NOV5
    r = await p6.inv.advance()
    assert r.waiting == 1 and await inv_status(p6, early.id) == "Waiting for Delivery Confirmation"
    assert await inv_status(p6, unshipped.id) == "Pending"                  # past its date, not shipped


async def test_walmart_uses_do_not_deliver_after(p6):
    o = await shipped_850(p6, "W-2", partner="WMT", extra={"DoNotDeliverAfter": "2026-11-20"})
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, o.id)).requested_delivery_date = date(2026, 11, 1)
        await u.commit()
    await p6.inv.advance()
    assert await inv_status(p6, o.id) == "Awaiting Requested Date"
    p6.clock.now = datetime(2026, 11, 20, 17, tzinfo=timezone.utc)
    await p6.inv.advance()
    assert await inv_status(p6, o.id) == "Waiting for Delivery Confirmation"


async def test_ac43_unconfirmed_for_30_days_stays_and_nothing_escalates(p6):
    o = await shipped_850(p6)
    await p6.inv.advance()
    p6.clock.now = NOV5 + timedelta(days=31)
    await p6.inv.advance()
    assert await inv_status(p6, o.id) == "Waiting for Delivery Confirmation"
    assert await p6.q("SELECT 1 FROM tasks WHERE type LIKE 'sales_invoice%'") == []


# ── confirmation and the invoice (AC-OB-42, FR-OB-30, 35) ────────────────────

async def test_ac42_nothing_without_a_confirmation_and_the_invoice_states_what_was_delivered(p6):
    o = await shipped_850(p6)
    await p6.inv.advance()
    assert await p6.q("SELECT 1 FROM sales_invoices") == []
    [line] = await p6.q("SELECT id FROM sales_order_lines WHERE order_id = :o", o=o.id)
    out = await confirm(p6, o.id, lines=[DeliveredLine(order_line_id=line["id"], delivered_qty=54)])
    assert (out.invoice_number, out.invoice_date, out.status) == (f"IN-{o.order_number}", date(2026, 11, 5), "Ready")
    [ln] = out.lines
    assert (ln.qty, ln.uom, ln.unit_price, ln.amount) == (9, "CA", Decimal("50.00000"), Decimal("450.00"))  # 54 of 60 EA
    assert out.total == Decimal("450.00")


async def test_a_part_case_is_never_invoiced_silently_short(p6):
    o = await shipped_850(p6, shipped=58)
    await p6.inv.advance()
    out = await confirm(p6, o.id)
    assert out.status == "Blocked" and out.lines[0].qty == 9
    [t] = await tasks(p6, "invoice_hold")
    assert "58 EA is not a whole number of cases of 6" in t
    with pytest.raises(BusinessError):
        await send(p6, out.id)
    async with p6.factory(OP) as u:
        await InvoiceService(u).release(p6.inv, ReleaseIn(order_id=o.id, reason="customer accepts 9 cases"))
        await u.commit()
    assert await inv_status(p6, o.id) == "Ready"


async def test_bulk_confirmation_takes_the_shipped_quantity_and_one_failure_stops_nothing(p6):
    a = await shipped_850(p6, "PO-1")
    b = await shipped_850(p6, "PO-3")
    await p6.inv.advance()
    async with p6.factory(OP) as u:
        res = await InvoiceService(u).confirm_bulk(p6.inv, ConfirmBulkIn(order_ids=[a.id, 999999, b.id],
                                                                         confirmed_date=date(2026, 11, 5)))
        await u.commit()
    assert [(r.order_id, r.ok) for r in res] == [(a.id, True), (999999, False), (b.id, True)]
    assert await inv_status(p6, a.id) == await inv_status(p6, b.id) == "Ready"


async def test_fr35_an_edit_is_audited_and_the_amount_follows(p6):
    o = await shipped_850(p6)
    await p6.inv.advance()
    out = await confirm(p6, o.id)
    async with p6.factory(OP) as u:
        ed = await InvoiceService(u).edit(p6.inv, out.id, InvoiceEdit(lines=[{"id": out.lines[0].id, "unit_price": "48.5"}]),
                                          out.version)
        await u.commit()
    assert ed.lines[0].amount == Decimal("485.00") and ed.total == Decimal("485.00")
    assert await p6.q("SELECT 1 FROM sales_audit WHERE entity = 'sales_invoice_lines' OR reason = 'invoice edited'")


async def test_confirming_needs_the_invoice_permission(p6):
    from app.platform.authorization import PermissionDenied
    o = await shipped_850(p6)
    await p6.inv.advance()

    async def read_only(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    p6.factory.permissions = read_only
    with pytest.raises(PermissionDenied):
        await confirm(p6, o.id)


# ── sending (FR-OB-30, 33, 34; A.9, A.12) ────────────────────────────────────

async def test_an_810_is_put_through_the_guard_and_a_resend_keeps_one_document(p6):
    o = await shipped_850(p6)
    await p6.inv.advance()
    out = await confirm(p6, o.id)
    await send(p6, out.id)
    r = await p6.worker.run_once()
    assert r.done == 1, await p6.q("SELECT error FROM sales_invoices")
    assert await inv_status(p6, o.id) == "Sent"
    [(path, data)] = [(p, d) for p, d in p6.store.files.items() if "/810-" in p]
    assert path.startswith(f"/prod/in/810-LBL-{o.order_number}-PO-1-")
    import json
    body = json.loads(data)["Invoice"][0]
    assert (body["AccountingID"], body["Invoice"], body["InvoiceDate"], body["Lines"][0]["QuantityShipped"]) == (
        "LBL", str(o.order_number), "2026-11-05", 10)
    await send(p6, out.id)  # resend: stays Sent
    await p6.worker.run_once()
    [inv] = await p6.q("SELECT send_count FROM sales_invoices")
    assert inv["send_count"] == 2 and await inv_status(p6, o.id) == "Sent"
    assert await p6.q("SELECT count(*) n FROM sales_documents WHERE kind = '810_JSON'") == [{"n": 1}]


async def email_partner(t, cc="ar@crm.example"):
    async with t.factory(SYSTEM_ACTOR) as u:
        p = (await u.session.execute(sa.select(Partner).where(Partner.code == "SOB"))).scalar_one()
        p.invoice_method, p.invoice_email, p.invoice_cc, p.name = "EMAIL", "ap@sobeys.example", cc, "Sobeys <b>&</b>"
        await u.commit()


async def test_ac45_the_invoice_email_goes_to_the_retailer_with_the_internal_copy_and_three_pdfs(p6):
    await email_partner(p6)
    o = await shipped_850(p6, "<script>9", partner="SOB")
    async with p6.factory(SYSTEM_ACTOR) as u:
        order = await u.session.get(Order, o.id)
        order.ship_to = {**(order.ship_to or {}), "name": "皇家奶粉 DC"}
        await u.commit()
    await p6.inv.advance()
    out = await confirm(p6, o.id)
    p6.edi.settings.SALES_EXTERNAL_EMAIL_ENABLED = True
    await send(p6, out.id)
    assert (await p6.worker.run_once()).done == 1
    [m] = p6.mail.sent
    assert m.to == ("ap@sobeys.example",) and m.cc == ("ar@crm.example",)
    assert m.subject == f"Order Documents | Sobeys <b>&</b> | {o.order_number} | <script>9"   # A.12 (a plain-text header)
    assert "&lt;script&gt;9" in m.body_html and "<script>" not in m.body_html                 # escaped (FR-OB-32)
    names = [n for n, _, _ in m.attachments]
    assert names == [f"IN-{o.order_number}.pdf", "PO-<script>9.pdf", f"PackingList-{o.order_number}.pdf"]
    assert all(d.startswith(b"%PDF") for _, d, _ in m.attachments)
    assert b"WenQuanYi" in m.attachments[0][1]  # the Chinese ship-to name: the embedded TrueType subset


async def test_with_the_email_switch_off_the_invoice_is_not_sent_and_the_would_have_sent_row_is_kept(p6):
    await email_partner(p6)
    o = await shipped_850(p6, "PO-9", partner="SOB")
    await p6.inv.advance()
    out = await confirm(p6, o.id)
    await send(p6, out.id)
    assert (await p6.worker.run_once()).suppressed == 1 and p6.mail.sent == []
    assert await inv_status(p6, o.id) == "Failed" and await tasks(p6, "invoice_failed") == []
    r = p6.refused[-1].context
    assert r["recipients"] == ["ap@sobeys.example", "ar@crm.example"] and r["subject"].startswith("Order Documents |")
    assert r["body_sha256"] and "body" not in r


# ── the shadow run ───────────────────────────────────────────────────────────

async def test_nothing_is_invoiced_during_the_shadow_run_or_for_a_shadow_order(p6):
    o = await shipped_850(p6)
    await p6.inv.advance()
    out = await confirm(p6, o.id)
    await send(p6, out.id)                                     # queued before the switch-on
    async with p6.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await u.commit()
    assert (await p6.worker.run_once()).cancelled == 1 and p6.store.calls == []
    assert await inv_status(p6, o.id) == "Failed"
    assert (await p6.inv.advance()).skipped == "shadow run: nothing is invoiced"
    with pytest.raises(BusinessError) as e:
        await send(p6, out.id)
    assert e.value.code == "invoice.shadow"
    async with p6.factory(SYSTEM_ACTOR) as u:                  # after the switch-off a shadow order stays Zoho's
        await SETTINGS.set(u.session, SHADOW_MODE, False)
        (await u.session.get(Order, o.id)).shadow = True
        await u.commit()
    with pytest.raises(BusinessError) as e:
        await send(p6, out.id)
    assert e.value.code == "invoice.shadow_order"


# ── holds: lot exception, return after shipment (FR-OB-25, FR-OB-19) ──────────

async def test_a_lot_exception_holds_the_invoice_until_released(p6):
    await run_850(p6.edi.env, {"PurchaseOrder": [po("LBL", "PO-7", [{"Line": 1, "BuyerPart": "L-555", "Quantity": 1, "UOM": "CA"}])]})
    o, _ = await p6.edi.env.order("PO-7")
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, o.id)).fulfillment_status = F.ACCEPTED_940.value
        await u.commit()
    await store_945(p6, x945(f"{o.order_number:09d}", ["LX*1", "W12*CP*6*6*0*EA**VN*CF0092", "W12*CP*1*1*0*EA**VN*CF0010"],
                             header=["DTM*011*20261102"], ctl="900000077"))
    await p6.ships.process_945s()
    r = await p6.inv.advance()
    assert r.held == 1 and await inv_status(p6, o.id) == "Blocked"
    [t] = await tasks(p6, "invoice_hold")
    assert "CF0010" in t


async def test_fr19_a_return_after_shipment_holds_the_invoice_and_raises_a_task(p6):
    o = await shipped_850(p6)
    await p6.inv.advance()
    [line] = await p6.q("SELECT id FROM sales_order_lines WHERE order_id = :o", o=o.id)
    async with p6.factory(OP) as u:
        out = await InvoiceService(u).record_return(p6.inv, ReturnIn(order_id=o.id, kind="Returned", reason="damaged",
                                                                     lines=[{"order_line_id": line["id"], "qty_ea": 6}]))
        await u.commit()
    assert out["invoice_status"] == "Blocked"
    [t] = await tasks(p6, "order_return")
    assert "returned — line 1: 6 EA" in t and "damaged" in t


# ── cancellation after the 940 (FR-OB-18, AC-OB-40; the email is P8) ─────────

async def test_ac40_cancelled_after_the_940_holds_with_one_task_stating_what_shipped(p6):
    from tests.test_orders import shopify_order
    from tests.test_outbound import sent_order
    o, _ = await sent_order(p6.edi)
    p6.edi.env.shop.orders = [shopify_order(1, 9301, updated=datetime(2026, 10, 30, tzinfo=timezone.utc),
                                            cancelled_at="2026-10-30T10:00:00Z")]
    await p6.edi.env.pull(datetime(2026, 10, 30, tzinfo=timezone.utc))
    assert (await p6.q("SELECT fulfillment_status FROM sales_orders WHERE id = :i", i=o.id))[0]["fulfillment_status"] == "On Hold"
    [t] = await tasks(p6, "cancel_after_940")
    assert "Nothing has shipped yet: tell the warehouse not to ship it." in t
