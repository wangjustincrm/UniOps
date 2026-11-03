"""The Order ref and the partner's name (user 2026-11-03): `<abbrev>-<source
number>` (a manual order: `<abbrev>-<internal number>`), unique, set once at
creation; the partner's abbreviation required and unique, a task for a partner
without one; the lists showing and searching the partner's name; the invoice's
delivery number, PO price, and a reason for a changed price."""
import base64
import re
import zlib
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

from app.domain.status import Fulfillment as F
from app.models.config import Partner
from app.platform.actors import SYSTEM_ACTOR
from app.schemas.invoices import InvoiceEdit
from app.schemas.orders import AddressIn, ManualLineIn, ManualOrderIn
from app.services.invoice_pdf import Party, PdfDoc, render
from app.services.invoice_views import InvoiceService
from app.services.order_views import ManualOrderService, OrderRepository
from app.services.problems import partners_without_abbreviation
from tests.test_invoicing import OP, confirm, p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import env, po, run_850, shopify_order  # noqa: F401 — env is a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture


def pdf_text(data: bytes) -> bytes:
    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        d = m.group(1).strip()
        for dec in (lambda x: zlib.decompress(base64.a85decode(x[:-2] if x.endswith(b"~>") else x)), zlib.decompress):
            try:
                out += dec(d)
                break
            except Exception:  # noqa: BLE001 — not this stream's encoding
                pass
    return out


async def test_each_source_gets_its_order_ref_and_it_never_changes(env):  # noqa: F811
    env.shop.orders = [shopify_order(1, 9301)]
    await env.pull()
    shop, _ = await env.order(1)
    assert shop.source_reference == "SHOP-9301"  # the test partner's abbreviation is its code
    await run_850(env, {"PurchaseOrder": [po("LBL", "4887314746", [{"Line": 1, "BuyerPart": "L-555", "Quantity": 1, "UOM": "CA"}])]})
    lbl, _ = await env.order("4887314746")
    assert lbl.source_reference == "LBL-4887314746"
    async with env.factory(SYSTEM_ACTOR) as u:  # the abbreviation changes later
        (await u.session.execute(sa.select(Partner).where(Partner.code == "LBL"))).scalar_one().abbrev = "LCL"
        await u.commit()
    await run_850(env, {"PurchaseOrder": [po("LBL", "4887314746", [{"Line": 1, "BuyerPart": "L-555", "Quantity": 2, "UOM": "CA"}])]})
    again, _ = await env.order("4887314746")
    assert again.source_reference == "LBL-4887314746"  # frozen at creation
    await run_850(env, {"PurchaseOrder": [po("LBL", "4887314999", [{"Line": 1, "BuyerPart": "L-555", "Quantity": 1, "UOM": "CA"}])]})
    new, _ = await env.order("4887314999")
    assert new.source_reference == "LCL-4887314999"  # new orders use the new one
    async with env.factory(OP) as u:
        m = await ManualOrderService(u).create(ManualOrderIn(partner_code="MAN", ship_to=AddressIn(
            name="Shop", phone="613-555-0100", address1="1 Main", city="Ottawa", province="ON", zip="K1A 0B1"),
            lines=[ManualLineIn(item_code="CF0092", quantity=Decimal(1), unit="EA")]))
        await u.commit()
    assert m.source_reference == f"MAN-{m.order_number}"


async def test_a_partner_without_abbreviation_reviews_its_orders_and_raises_one_task_until_set(env):  # noqa: F811
    async with env.factory(SYSTEM_ACTOR) as u:
        (await u.session.execute(sa.select(Partner).where(Partner.code == "SOB"))).scalar_one().abbrev = None
        await u.commit()
    await run_850(env, {"PurchaseOrder": [po("SOB", "S-1", [{"Line": 1, "BuyerPart": "S-777", "Quantity": 1, "UOM": "CA"}])]})
    o, _ = await env.order("S-1")
    assert o.fulfillment_status == F.REVIEW and "no abbreviation" in o.review_reason and o.source_reference is None
    async with env.factory(SYSTEM_ACTOR) as u:
        assert await partners_without_abbreviation(u.session, env.tasks) == 1
        assert await partners_without_abbreviation(u.session, env.tasks) == 1  # the next run refreshes the same task
        await u.commit()
    [t] = await env.q("SELECT title, is_completed FROM tasks WHERE type = 'sales_partner_no_abbrev'")
    assert t["title"] == "Partner SOB needs an abbreviation" and not t["is_completed"]
    from app.schemas.partners import PartnerUpdate
    from app.services.partners import PartnerService
    async with env.factory(OP) as u:
        p = (await u.session.execute(sa.select(Partner).where(Partner.code == "SOB"))).scalar_one()
        await PartnerService(u).update(p.id, PartnerUpdate(abbrev="sob"), p.version)
        await u.commit()
    [t] = await env.q("SELECT is_completed FROM tasks WHERE type = 'sales_partner_no_abbrev'")
    assert t["is_completed"] is True
    [row] = await env.q("SELECT abbrev FROM sales_partners WHERE code = 'SOB'")
    assert row["abbrev"] == "SOB"  # upper-cased


async def test_the_orders_list_carries_and_searches_the_partner_name_and_the_ref(env):  # noqa: F811
    from app.platform.query import QuerySpec
    await run_850(env, {"PurchaseOrder": [po("LBL", "4887314746", [{"Line": 1, "BuyerPart": "L-555", "Quantity": 1, "UOM": "CA"}])]})
    async with env.factory(OP) as u:
        repo = OrderRepository(u)
        page = await repo.list(QuerySpec(search="Loblaws"))
        assert [(o.source_reference, o.partner_name) for o in page.items] == [("LBL-4887314746", "Loblaws")]
        assert (await repo.list(QuerySpec(search="LBL-48873"))).total == 1
        assert {f["name"] for f in repo.field_meta()} >= {"partner_name", "order_state"}


# ── the invoice: delivery number, PO price, a reason for a new price ─────────
async def test_the_invoice_shows_its_po_price_takes_a_delivery_number_and_a_price_change_needs_a_reason(p6):  # noqa: F811
    o = await shipped_850(p6, price="50")
    await p6.inv.advance()
    out = await confirm(p6, o.id)
    assert (out.order_ref, out.partner_name) == ("LBL-PO-1", "Loblaws")
    assert out.lines[0].po_price == Decimal("50.00000")  # the 850's 50 per CA: per case, as invoiced
    async with p6.factory(OP) as u:
        svc = InvoiceService(u)
        try:
            await svc.edit(p6.inv, out.id, InvoiceEdit(lines=[{"id": out.lines[0].id, "unit_price": "48"}]), out.version)
            raise AssertionError("a price change without a reason was taken")
        except Exception as e:  # noqa: BLE001
            assert getattr(e, "code", "") == "invoice.reason_required"
    async with p6.factory(OP) as u:
        ed = await InvoiceService(u).edit(p6.inv, out.id, InvoiceEdit(
            delivery_number="POD-4471", reason="retailer's agreed price",
            lines=[{"id": out.lines[0].id, "unit_price": "48"}]), out.version)
        await u.commit()
    assert ed.delivery_number == "POD-4471" and ed.lines[0].unit_price == Decimal("48")
    assert ed.lines[0].po_price == Decimal("50.00000")  # the PO's stays beside it
    [a] = await p6.q("SELECT reason, before, after FROM sales_audit WHERE entity = 'sales_invoices' AND reason LIKE 'invoice edited:%'")
    assert a["reason"] == "invoice edited: retailer's agreed price" and a["after"]["delivery_number"] == "POD-4471"
    assert await p6.q("SELECT 1 FROM sales_log WHERE message LIKE '%price changed — retailer''s agreed price: line 1 CF0092 50.00000 → 48%'")
    async with p6.factory(OP) as u:  # the delivery number alone needs no reason, and can be cleared
        ed2 = await InvoiceService(u).edit(p6.inv, out.id, InvoiceEdit(delivery_number=None), ed.version)
        await u.commit()
    assert ed2.delivery_number is None


def test_the_pdf_shows_the_order_ref_the_internal_number_and_the_delivery_number():
    data = render(PdfDoc(title="Invoice", number="IN-7", when=date(2026, 11, 5), po="4887314746", order_number=7,
                         order_ref="LCL-4887314746", delivery_number="POD-4471", seller=Party(name="S"),
                         buyer=Party(name="B"), ship_to=Party(name="T"), lines=[]))
    text = pdf_text(data)
    assert b"Order: LCL-4887314746" in text and b"Internal no.: 7" in text and b"Delivery no.: POD-4471" in text


async def test_the_partner_abbreviation_is_required_and_unique(env):  # noqa: F811
    from app.schemas.partners import PartnerCreate
    import pytest
    from pydantic import ValidationError
    base = dict(code="NEW", name="New", channel="TRUECOMMERCE", settlement_mode="INVOICE", sending_mode="AUTOMATIC",
                delay_minutes=0, invoice_method="810_JSON", dc_routing_required=False)
    with pytest.raises(ValidationError):
        PartnerCreate(**base)
    with pytest.raises(ValidationError):
        PartnerCreate(**base, abbrev="L-C")  # letters and digits only
    from app.services.partners import PartnerService
    async with env.factory(OP) as u:
        try:
            await PartnerService(u).create(PartnerCreate(**base, abbrev="lbl"))  # LBL's, in another case
            raise AssertionError("a second LBL was taken")
        except Exception as e:  # noqa: BLE001
            assert getattr(e, "code", "") == "partner.abbrev_taken"
