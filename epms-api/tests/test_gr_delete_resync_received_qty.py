"""Deleting or un-counting a GR must give the PO's received_qty back.

``po_line_items.received_qty`` is written by ``crud.gr._update_po_received_qty``
as a pure accumulator (``+=``) on collect/confirm, and nothing ever subtracted
from it. Data Maintenance could therefore delete a GR -- or flip its status to
cancelled -- and leave the PO permanently claiming goods it never kept, which
eats the outstanding quantity a follow-up GR would need and pins the PO at
fully_received forever.

Note the test document numbers all carry a non-digit suffix on purpose:
``crud/_numbering.next_number()`` treats any same-prefix, all-decimal tail as an
allocated sequence number, so purely numeric literals would poison the real
counter.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _tag() -> str:
    return uuid.uuid4().hex[:6]


async def _seed_po(factory, *, po_status="fully_received", line_qty="10"):
    """A vendor + user + one-line PO, with no receipts against it yet."""
    from app.models.vendor import Vendor
    from app.models.user import User
    from app.models.po import PurchaseOrder, PoLineItem

    tag = _tag()
    vendor_id, user_id, po_id, po_line_id = (uuid.uuid4() for _ in range(4))
    async with factory() as db:
        db.add(Vendor(id=vendor_id, code=f"V-{tag}", name="Resync Vendor",
                      category="supplier", contact_name="A", contact_email=f"a-{tag}@x.com"))
        db.add(User(id=user_id, email=f"u-{tag}@x.com", hashed_password="x",
                    full_name="Resync User", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number=f"PO-RSYNC-{tag}", title="Resync PO", type=1,
                             status=po_status, currency="CAD",
                             subtotal=Decimal("100"), tax_rate=Decimal("0"),
                             tax_amount=Decimal("0"), total=Decimal("100"),
                             vendor_id=vendor_id, vendor_name="Resync Vendor",
                             created_by=user_id))
        await db.flush()
        db.add(PoLineItem(id=po_line_id, po_id=po_id, description="Widget",
                          qty=Decimal(line_qty), unit="ea", unit_price=Decimal("10"),
                          line_total=Decimal("100"), received_qty=Decimal("0")))
        await db.commit()
    return {"vendor_id": vendor_id, "user_id": user_id, "po_id": po_id,
            "po_line_id": po_line_id, "tag": tag}


async def _add_gr(factory, ctx, *, status, qty, actual_qty=None, po_line_id=...):
    """A GR against the seeded PO.

    Seeding the PO's received_qty separately (via ``_bump_received``) rather than
    driving the real confirm action -- which needs approval-api -- reproduces
    exactly the state the accumulator leaves behind. Only statuses that actually
    reached the accumulator get a contribution; the caller controls that.
    """
    from app.models.gr import GoodsReceipt, GrLineItem

    gr_id = uuid.uuid4()
    line_id = ctx["po_line_id"] if po_line_id is ... else po_line_id
    po_number = f"PO-RSYNC-{ctx['tag']}"
    async with factory() as db:
        db.add(GoodsReceipt(id=gr_id, number=f"GR-RSYNC-{_tag()}", title="Resync GR",
                            status=status, po_id=ctx["po_id"], po_number=po_number,
                            vendor_id=ctx["vendor_id"], vendor_name="Resync Vendor",
                            gr_type="physical", procurement_type=1, currency="CAD",
                            created_by=ctx["user_id"]))
        await db.flush()
        db.add(GrLineItem(gr_id=gr_id, po_line_id=line_id, description="Widget",
                          qty_ordered=Decimal("10"), qty_received=Decimal(qty),
                          actual_qty=None if actual_qty is None else Decimal(actual_qty),
                          unit="ea", unit_price=Decimal("10"), line_total=Decimal("10")))
        await db.commit()
    return gr_id


async def _bump_received(factory, ctx, amount):
    """Mimic the accumulator: add this GR's contribution to the PO line."""
    from app.models.po import PoLineItem

    async with factory() as db:
        line = (await db.execute(
            select(PoLineItem).where(PoLineItem.id == ctx["po_line_id"]))).scalar_one()
        line.received_qty = line.received_qty + Decimal(amount)
        await db.commit()


async def _read_po(factory, ctx):
    from app.models.po import PurchaseOrder, PoLineItem

    async with factory() as db:
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == ctx["po_id"]))).scalar_one()
        line = (await db.execute(
            select(PoLineItem).where(PoLineItem.id == ctx["po_line_id"]))).scalar_one()
        return po.status, line.received_qty


async def _delete_gr(factory, gr_id):
    from app.models.gr import GoodsReceipt
    from app.admin.registry import REGISTRY

    async with factory() as db:
        gr = (await db.execute(
            select(GoodsReceipt).where(GoodsReceipt.id == gr_id))).scalar_one()
        summary = await REGISTRY["gr"].cascade_delete(db, gr)
        await db.commit()
        return summary


@pytest.mark.asyncio
async def test_delete_one_of_two_grs_gives_back_only_its_share(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory)
    await _add_gr(factory, ctx, status="confirmed", qty="6")
    await _bump_received(factory, ctx, "6")
    doomed = await _add_gr(factory, ctx, status="confirmed", qty="4")
    await _bump_received(factory, ctx, "4")
    assert await _read_po(factory, ctx) == ("fully_received", Decimal("10.0000"))

    await _delete_gr(factory, doomed)

    status, received = await _read_po(factory, ctx)
    assert received == Decimal("6.0000"), "surviving GR's 6 must remain, the deleted 4 must go"
    assert status == "partially_received", "PO can no longer claim to be fully received"


@pytest.mark.asyncio
async def test_delete_last_gr_returns_po_to_issued(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory)
    only = await _add_gr(factory, ctx, status="confirmed", qty="10")
    await _bump_received(factory, ctx, "10")

    await _delete_gr(factory, only)

    assert await _read_po(factory, ctx) == ("issued", Decimal("0.0000"))


@pytest.mark.asyncio
async def test_deleting_a_gr_that_never_counted_changes_nothing(test_engine):
    """A pending_ack GR never reached the accumulator -- subtracting it would
    silently under-report goods that really were received."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory, po_status="partially_received")
    await _add_gr(factory, ctx, status="confirmed", qty="6")
    await _bump_received(factory, ctx, "6")
    never_counted = await _add_gr(factory, ctx, status="pending_ack", qty="4")

    await _delete_gr(factory, never_counted)

    assert await _read_po(factory, ctx) == ("partially_received", Decimal("6.0000"))


@pytest.mark.asyncio
async def test_recompute_prefers_actual_qty_over_qty_received(test_engine):
    """The accumulator uses COALESCE(actual_qty, qty_received); the give-back must
    use the same figure or a short-shipped line drifts on every delete."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory, po_status="partially_received")
    await _add_gr(factory, ctx, status="collected", qty="6", actual_qty="5")
    await _bump_received(factory, ctx, "5")
    doomed = await _add_gr(factory, ctx, status="confirmed", qty="2")
    await _bump_received(factory, ctx, "2")

    await _delete_gr(factory, doomed)

    _, received = await _read_po(factory, ctx)
    assert received == Decimal("5.0000")


@pytest.mark.asyncio
async def test_discrepancy_gr_still_counts(test_engine):
    """`discrepancy` is only reachable from `collected`, so its quantity is already
    in the PO -- dropping it from the recompute would erase real receipts."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory, po_status="partially_received")
    await _add_gr(factory, ctx, status="discrepancy", qty="7")
    await _bump_received(factory, ctx, "7")
    doomed = await _add_gr(factory, ctx, status="confirmed", qty="1")
    await _bump_received(factory, ctx, "1")

    await _delete_gr(factory, doomed)

    assert await _read_po(factory, ctx) == ("partially_received", Decimal("7.0000"))


@pytest.mark.asyncio
async def test_resync_leaves_po_outside_the_receipt_states_alone(test_engine):
    """A closed PO must not be dragged back to issued by a Data Maintenance delete."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory, po_status="closed")
    only = await _add_gr(factory, ctx, status="confirmed", qty="10")
    await _bump_received(factory, ctx, "10")

    await _delete_gr(factory, only)

    status, received = await _read_po(factory, ctx)
    assert status == "closed", "status guard must match the accumulator's"
    assert received == Decimal("0.0000"), "quantities are still corrected"


@pytest.mark.asyncio
async def test_gr_line_not_linked_to_a_po_line_is_ignored(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory, po_status="partially_received")
    await _add_gr(factory, ctx, status="confirmed", qty="6")
    await _bump_received(factory, ctx, "6")
    doomed = await _add_gr(factory, ctx, status="confirmed", qty="4", po_line_id=None)

    await _delete_gr(factory, doomed)

    assert await _read_po(factory, ctx) == ("partially_received", Decimal("6.0000"))


@pytest.mark.asyncio
async def test_po_cascade_delete_still_removes_everything(test_engine):
    """The give-back must not disturb the PO cascade, whose lines are on their way out."""
    from sqlalchemy import func
    from app.models.po import PurchaseOrder, PoLineItem
    from app.models.gr import GoodsReceipt
    from app.admin.registry import REGISTRY

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory)
    await _add_gr(factory, ctx, status="confirmed", qty="10")
    await _bump_received(factory, ctx, "10")

    async with factory() as db:
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == ctx["po_id"]))).scalar_one()
        summary = await REGISTRY["po"].cascade_delete(db, po)
        await db.commit()
    assert summary["goods_receipts"] == 1 and summary["purchase_orders"] == 1

    async with factory() as db:
        for model, col in ((PurchaseOrder, PurchaseOrder.id), (PoLineItem, PoLineItem.po_id),
                           (GoodsReceipt, GoodsReceipt.po_id)):
            cnt = (await db.execute(
                select(func.count()).select_from(model).where(col == ctx["po_id"]))).scalar_one()
            assert cnt == 0, f"{model.__name__} survived the PO cascade"


# -- GR status edited in Data Maintenance --------------------------------------

@pytest.mark.asyncio
async def test_editing_gr_status_to_cancelled_gives_the_quantity_back(test_engine):
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory)
    gr_id = await _add_gr(factory, ctx, status="confirmed", qty="10")
    await _bump_received(factory, ctx, "10")

    async with factory() as db:
        await service.edit_record(db, "gr", gr_id, {"status": "cancelled"},
                                  actor_id=ctx["user_id"], actor_email="admin@x.com")
        await db.commit()

    assert await _read_po(factory, ctx) == ("issued", Decimal("0.0000"))


@pytest.mark.asyncio
async def test_editing_gr_status_back_to_confirmed_takes_the_quantity_again(test_engine):
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory, po_status="issued")
    gr_id = await _add_gr(factory, ctx, status="cancelled", qty="10")

    async with factory() as db:
        await service.edit_record(db, "gr", gr_id, {"status": "confirmed"},
                                  actor_id=ctx["user_id"], actor_email="admin@x.com")
        await db.commit()

    assert await _read_po(factory, ctx) == ("fully_received", Decimal("10.0000"))


@pytest.mark.asyncio
async def test_editing_a_gr_without_touching_status_leaves_quantities_alone(test_engine):
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory, po_status="partially_received")
    gr_id = await _add_gr(factory, ctx, status="confirmed", qty="6")
    await _bump_received(factory, ctx, "6")

    async with factory() as db:
        await service.edit_record(db, "gr", gr_id, {"title": "renamed"},
                                  actor_id=ctx["user_id"], actor_email="admin@x.com")
        await db.commit()

    assert await _read_po(factory, ctx) == ("partially_received", Decimal("6.0000"))


# -- Manual received_qty on the PO line editor ---------------------------------

def test_po_line_editor_exposes_received_qty():
    from app.admin.registry import REGISTRY

    child = REGISTRY["po"].schema.child
    spec = next((f for f in child.fields if f.name == "received_qty"), None)
    assert spec is not None, "PO line editor has no received_qty field"
    assert spec.editable is True and spec.type == "decimal"


@pytest.mark.asyncio
async def test_editing_received_qty_by_hand_updates_the_po_status(test_engine):
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ctx = await _seed_po(factory, po_status="issued")

    async with factory() as db:
        await service.edit_record(db, "po", ctx["po_id"], {"line_items": [{
            "id": str(ctx["po_line_id"]), "description": "Widget", "qty": "10",
            "unit": "ea", "unit_price": "10", "received_qty": "4",
        }]}, actor_id=ctx["user_id"], actor_email="admin@x.com")
        await db.commit()

    assert await _read_po(factory, ctx) == ("partially_received", Decimal("4.0000"))
