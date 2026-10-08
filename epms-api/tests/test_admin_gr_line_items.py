"""Data Maintenance: editing Goods Receipt line items.

A GR line is not a free-standing number: its received quantity is summed into the
PO's received_qty (and through it the PO's receipt status), and its line total is
snapshotted onto every invoice matched to the GR as gr_value. An edit here has to
drag both along, or the repair leaves the PO and 3-Way Match describing the
receipt as it was before the fix.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _factory(test_engine):
    # autoflush=False like production's session
    return async_sessionmaker(test_engine, class_=AsyncSession,
                              expire_on_commit=False, autoflush=False)


async def _seed(factory, *, gr_status="confirmed", actual_qty=None):
    """PO (2 lines × qty 10) + one GR receiving 10 / 4 of them + a matched invoice."""
    from app.models.gr import GoodsReceipt, GrLineItem
    from app.models.invoice import Invoice
    from app.models.po import PurchaseOrder, PoLineItem
    from app.models.user import User
    from app.models.vendor import Vendor

    t = uuid.uuid4().hex[:6]
    vid = uuid.uuid4(); uid = uuid.uuid4(); po_id = uuid.uuid4(); gr_id = uuid.uuid4()
    pol_a = uuid.uuid4(); pol_b = uuid.uuid4(); grl_a = uuid.uuid4(); grl_b = uuid.uuid4()
    inv_id = uuid.uuid4()
    async with factory() as db:
        db.add(Vendor(id=vid, code=f"V-{t}", name="V", category="supplier",
                      contact_name="A", contact_email="a@x.com"))
        db.add(User(id=uid, email=f"gr-{t}@x.com", hashed_password="x",
                    full_name="U", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number=f"PO-{t}", title="PO", type=1,
                             status="partially_received", currency="CAD",
                             subtotal=Decimal("200"), tax_rate=Decimal("0"),
                             tax_amount=Decimal("0"), total=Decimal("200"),
                             vendor_id=vid, vendor_name="V", created_by=uid))
        await db.flush()
        for lid, so in ((pol_a, 0), (pol_b, 1)):
            db.add(PoLineItem(id=lid, po_id=po_id, description="item", qty=Decimal("10"),
                              unit="ea", unit_price=Decimal("10"), line_total=Decimal("100"),
                              received_qty=Decimal("10") if lid == pol_a else Decimal("4"),
                              sort_order=so))
        await db.commit()
    async with factory() as db:
        db.add(GoodsReceipt(id=gr_id, number=f"GR-{t}", title="GR", status=gr_status,
                            po_id=po_id, po_number=f"PO-{t}", vendor_id=vid, vendor_name="V",
                            gr_type="physical", procurement_type=1, currency="CAD",
                            created_by=uid))
        await db.flush()
        db.add(GrLineItem(id=grl_a, gr_id=gr_id, po_line_id=pol_a, description="a",
                          qty_ordered=Decimal("10"), qty_received=Decimal("10"),
                          actual_qty=actual_qty, unit="ea", unit_price=Decimal("10"),
                          line_total=Decimal("100"), sort_order=0))
        db.add(GrLineItem(id=grl_b, gr_id=gr_id, po_line_id=pol_b, description="b",
                          qty_ordered=Decimal("10"), qty_received=Decimal("4"),
                          unit="ea", unit_price=Decimal("10"), line_total=Decimal("40"),
                          sort_order=1))
        await db.flush()
        db.add(Invoice(id=inv_id, internal_ref=f"INV-{t}", vendor_invoice_number=f"VI-{t}",
                       vendor_id=vid, vendor_name="V", status="matched", po_id=po_id,
                       gr_id=gr_id, gr_ids=[str(gr_id)], gr_number=f"GR-{t}",
                       gr_value=Decimal("140"),
                       amount=Decimal("140"), tax_amount=Decimal("0"),
                       total_amount=Decimal("140"), currency="CAD",
                       invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
                       line_items=[], uploaded_by=uid))
        await db.commit()
    return dict(uid=uid, po_id=po_id, gr_id=gr_id, pol_a=pol_a, pol_b=pol_b,
                grl_a=grl_a, grl_b=grl_b, inv_id=inv_id)


def _line(lid, desc, qty_received, unit_price="10", **extra):
    return {"id": str(lid), "description": desc, "qty_ordered": "10",
            "qty_received": qty_received, "unit": "ea", "unit_price": unit_price,
            "condition": "good", **extra}


async def _state(factory, s):
    from app.models.gr import GrLineItem
    from app.models.invoice import Invoice
    from app.models.po import PurchaseOrder, PoLineItem
    async with factory() as db:
        po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == s["po_id"]))).scalar_one()
        pol = {l.id: l for l in (await db.execute(
            select(PoLineItem).where(PoLineItem.po_id == s["po_id"]))).scalars().all()}
        grl = {l.id: l for l in (await db.execute(
            select(GrLineItem).where(GrLineItem.gr_id == s["gr_id"]))).scalars().all()}
        inv = (await db.execute(select(Invoice).where(Invoice.id == s["inv_id"]))).scalar_one()
        return po, pol, grl, inv


def test_gr_schema_exposes_line_items_keyed_on_qty_received():
    from app.admin.registry import REGISTRY

    child = REGISTRY["gr"].schema.child
    assert child is not None and child.fk_field == "gr_id"
    d = REGISTRY["gr"].schema.to_dict()["child"]
    assert d["qty_field"] == "qty_received" and d["price_field"] == "unit_price"
    assert d["allow_add"] is False
    assert not any(f.name == "line_total" and f.editable for f in child.fields)
    # PR/PO/PA keep the old contract
    for key in ("pr", "po", "pa"):
        cd = REGISTRY[key].schema.to_dict()["child"]
        assert cd["qty_field"] == "qty" and cd["allow_add"] is True


@pytest.mark.asyncio
async def test_edit_gr_line_qty_resyncs_po_and_invoice_gr_value(test_engine):
    from app.admin import service

    f = _factory(test_engine)
    s = await _seed(f)
    async with f() as db:
        after = await service.edit_record(db, "gr", s["gr_id"], {"line_items": [
            _line(s["grl_a"], "a", "10"),
            _line(s["grl_b"], "b fixed", "10"),          # 4 -> 10
        ]}, actor_id=s["uid"], actor_email="admin@x.com")
        await db.commit()

    po, pol, grl, inv = await _state(f, s)
    assert grl[s["grl_b"]].description == "b fixed"
    assert grl[s["grl_b"]].line_total == Decimal("100.00")
    assert pol[s["pol_b"]].received_qty == Decimal("10")
    assert po.status == "fully_received"
    assert inv.gr_value == Decimal("200.00")
    assert after["po_lines_received_qty_resynced"] == 1
    assert after["invoices_gr_value_refreshed"] == 1


@pytest.mark.asyncio
async def test_edit_gr_line_price_moves_invoice_gr_value(test_engine):
    from app.admin import service

    f = _factory(test_engine)
    s = await _seed(f)
    async with f() as db:
        await service.edit_record(db, "gr", s["gr_id"], {"line_items": [
            _line(s["grl_a"], "a", "10", unit_price="12.5"),
            _line(s["grl_b"], "b", "4"),
        ]}, actor_id=s["uid"], actor_email="admin@x.com")
        await db.commit()
    po, pol, grl, inv = await _state(f, s)
    assert grl[s["grl_a"]].line_total == Decimal("125.00")
    assert inv.gr_value == Decimal("165.00")
    assert pol[s["pol_a"]].received_qty == Decimal("10")   # quantities untouched


@pytest.mark.asyncio
async def test_deleting_a_gr_line_gives_its_quantity_back_to_the_po(test_engine):
    from app.admin import service

    f = _factory(test_engine)
    s = await _seed(f)
    async with f() as db:
        await service.edit_record(db, "gr", s["gr_id"], {"line_items": [
            _line(s["grl_a"], "a", "10"),
        ]}, actor_id=s["uid"], actor_email="admin@x.com")
        await db.commit()
    po, pol, grl, inv = await _state(f, s)
    assert set(grl) == {s["grl_a"]}
    assert pol[s["pol_b"]].received_qty == Decimal("0")
    assert inv.gr_value == Decimal("100.00")


@pytest.mark.asyncio
async def test_clearing_actual_qty_hands_the_count_back_to_qty_received(test_engine):
    """A cleared input arrives as "" and must become NULL — dropping it kept the
    old value, so a nullable line field could be set here but never cleared."""
    from app.admin import service

    f = _factory(test_engine)
    s = await _seed(f, actual_qty=Decimal("7"))
    async with f() as db:
        # first a no-op edit so the seeded actual_qty=7 is what the PO counts
        await service.edit_record(db, "gr", s["gr_id"], {"line_items": [
            _line(s["grl_a"], "a", "10"), _line(s["grl_b"], "b", "4"),
        ]}, actor_id=s["uid"], actor_email="admin@x.com")
        await db.commit()
    po, pol, grl, _ = await _state(f, s)
    assert grl[s["grl_a"]].actual_qty == Decimal("7")
    assert pol[s["pol_a"]].received_qty == Decimal("7")

    async with f() as db:
        await service.edit_record(db, "gr", s["gr_id"], {"line_items": [
            _line(s["grl_a"], "a", "10", actual_qty=""), _line(s["grl_b"], "b", "4"),
        ]}, actor_id=s["uid"], actor_email="admin@x.com")
        await db.commit()
    po, pol, grl, _ = await _state(f, s)
    assert grl[s["grl_a"]].actual_qty is None
    assert pol[s["pol_a"]].received_qty == Decimal("10")


@pytest.mark.asyncio
async def test_new_gr_line_is_refused_and_nothing_changes(test_engine):
    from app.admin import service

    f = _factory(test_engine)
    s = await _seed(f)
    async with f() as db:
        with pytest.raises(ValueError, match="cannot be added"):
            await service.edit_record(db, "gr", s["gr_id"], {"line_items": [
                _line(s["grl_a"], "a", "10"), _line(s["grl_b"], "b", "4"),
                {"description": "orphan", "qty_ordered": "1", "qty_received": "1",
                 "unit": "ea", "unit_price": "1", "condition": "good"},
            ]}, actor_id=s["uid"], actor_email="admin@x.com")
        await db.rollback()
    _, _, grl, inv = await _state(f, s)
    assert len(grl) == 2 and inv.gr_value == Decimal("140")


@pytest.mark.asyncio
async def test_lines_on_an_uncounted_gr_do_not_count_toward_the_po(test_engine):
    """pending_ack is not a counted status, so its edited lines must not land in
    received_qty — the PO is re-derived from counted GRs only (here: none; the
    seed's 10/4 were hand-set and get corrected, same as a status edit does)."""
    from app.admin import service

    f = _factory(test_engine)
    s = await _seed(f, gr_status="pending_ack")
    async with f() as db:
        await service.edit_record(db, "gr", s["gr_id"], {"line_items": [
            _line(s["grl_a"], "a", "3"), _line(s["grl_b"], "b", "3"),
        ]}, actor_id=s["uid"], actor_email="admin@x.com")
        await db.commit()
    po, pol, grl, inv = await _state(f, s)
    # resync recomputes from counted GRs only → none → 0, which is the truth
    assert pol[s["pol_a"]].received_qty == Decimal("0")
    assert pol[s["pol_b"]].received_qty == Decimal("0")
    assert inv.gr_value == Decimal("60.00")
