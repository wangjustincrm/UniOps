"""app/services/in_transit.py — what is on order, and the four things that are not.

Every exclusion here exists because including it produced a wrong number
against real data, and each test records that number. Without them the rules
read like arbitrary caution and the next person deletes one.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.epms_mirror import EpmsPoLineItem, EpmsPurchaseOrder, MdmMaterial
from app.services.in_transit import (
    in_transit_by_material,
    open_lines_for_material,
    raw_milk_material_codes,
)


async def _po(db, *, status="issued", type_=1, eta=None, number=None):
    po = EpmsPurchaseOrder(
        id=uuid.uuid4(),
        number=number or f"PO-{uuid.uuid4().hex[:8]}",
        type=type_,
        status=status,
        vendor_name="Test Vendor",
        expected_delivery=eta,
    )
    db.add(po)
    await db.commit()
    return po


async def _line(db, po, code, qty, received, arrival=None, unit="KGM"):
    db.add(EpmsPoLineItem(
        id=uuid.uuid4(), po_id=po.id, material_id=code,
        qty=Decimal(qty), received_qty=Decimal(received),
        unit=unit, planned_arrival_date=arrival,
    ))
    await db.commit()


async def _material(db, code, *, erp_class="0102", name=None):
    db.add(MdmMaterial(
        id=uuid.uuid4(), code=code, name=name or f"Material {code}",
        base_uom="KGM", shelf_life_months=None,
        erp_class_code=erp_class, erp_class_name=None,
    ))
    await db.commit()


# ── the remainder, not the order ─────────────────────────────────────────


@pytest.mark.anyio
async def test_only_the_unreceived_remainder_is_in_transit(db_session):
    po = await _po(db_session)
    await _line(db_session, po, "CR0025", "1000", "400")
    result = await in_transit_by_material(db_session)
    assert result["CR0025"].qty == Decimal("600")
    assert result["CR0025"].open_lines == 1


@pytest.mark.anyio
async def test_fully_received_lines_do_not_linger(db_session):
    po = await _po(db_session)
    await _line(db_session, po, "CR0025", "500", "500")
    assert "CR0025" not in await in_transit_by_material(db_session)


@pytest.mark.anyio
async def test_quantities_add_up_across_several_orders(db_session):
    for _ in range(3):
        po = await _po(db_session)
        await _line(db_session, po, "CR0025", "100", "10")
    result = await in_transit_by_material(db_session)
    assert result["CR0025"].qty == Decimal("270")
    assert result["CR0025"].open_lines == 3


# ── the four exclusions ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_raw_milk_is_excluded_by_its_erp_class_not_its_code(db_session):
    """Business rule 2026-08-17: NC material class 0101 Raw Milk is never
    counted as stock or as on order. Classified by CLASS, never by code prefix:
    CR0059 "Pasteurized Milk" is 0101 while carrying an ordinary raw-material
    prefix, so a prefix rule is wrong in both directions. Both codes below share
    the CR prefix and only one is milk.
    """
    await _material(db_session, "CR0059", erp_class="0101", name="Pasteurized Milk")
    await _material(db_session, "CR0025", erp_class="0102", name="Lactose")
    po = await _po(db_session)
    await _line(db_session, po, "CR0059", "1000", "0")
    await _line(db_session, po, "CR0025", "1000", "0")

    result = await in_transit_by_material(db_session)
    assert "CR0059" not in result, "class 0101 must not be counted"
    assert result["CR0025"].qty == Decimal("1000")


@pytest.mark.anyio
async def test_nc_milk_pos_are_excluded_so_a_negative_remainder_cannot_appear(db_session):
    """The arithmetic guard, distinct from the classification rule. Raw-milk
    receipt and stock-in happen in NC, not UniOps, so received_qty is backfilled
    loosely and regularly EXCEEDS the order: CR0180 nets -1,648,350, CR0010
    -1,525,432, and all 238 such lines together -2,695,743. Counting them would
    report negative quantities on order for the plant's biggest materials.
    """
    await _material(db_session, "CR0180", erp_class="0101")
    po = await _po(db_session, status="nc_milk")
    await _line(db_session, po, "CR0180", "1000", "2648350")
    assert await in_transit_by_material(db_session) == {}


@pytest.mark.anyio
async def test_orders_not_yet_placed_are_not_on_their_way(db_session):
    """`approved` means somebody signed it, not that a supplier has it."""
    for status in ("draft", "in_review", "approved", "rejected", "cancelled",
                   "fully_received", "closed"):
        po = await _po(db_session, status=status)
        await _line(db_session, po, f"CR-{status}", "100", "0")
    result = await in_transit_by_material(db_session)
    assert result == {}, f"expected nothing on order, got {sorted(result)}"


@pytest.mark.anyio
async def test_partially_received_orders_are_still_on_their_way(db_session):
    po = await _po(db_session, status="partially_received")
    await _line(db_session, po, "CR0025", "100", "30")
    assert (await in_transit_by_material(db_session))["CR0025"].qty == Decimal("70")


@pytest.mark.anyio
async def test_non_material_po_types_are_excluded(db_session):
    """Types 2-6 are consumables, spare parts, service, fixed assets, software.
    893 of the open lines company-wide are these and not one carries a material
    code, so they could not be attributed to a material even if they belonged
    here."""
    for type_ in (2, 3, 4, 5, 6):
        po = await _po(db_session, type_=type_)
        await _line(db_session, po, "CR0025", "100", "0")
    assert await in_transit_by_material(db_session) == {}


@pytest.mark.anyio
async def test_lines_without_a_material_code_are_skipped(db_session):
    po = await _po(db_session)
    db_session.add(EpmsPoLineItem(
        id=uuid.uuid4(), po_id=po.id, material_id=None,
        qty=Decimal("100"), received_qty=Decimal("0"), unit="EA",
        planned_arrival_date=None))
    await db_session.commit()
    assert await in_transit_by_material(db_session) == {}


@pytest.mark.anyio
async def test_a_material_the_master_has_not_caught_up_with_is_still_counted(db_session):
    """★ The SQL trap in the raw-milk exclusion: `erp_class_code != '0101'`
    evaluates to NULL — not true — for a material with no master row, so a plain
    comparison would DROP the line. That is a real inbound quantity, and losing
    it understates what is coming. An unknown class is not raw milk.
    """
    po = await _po(db_session)
    await _line(db_session, po, "CR-BRAND-NEW", "250", "0")   # no MdmMaterial row
    result = await in_transit_by_material(db_session)
    assert result["CR-BRAND-NEW"].qty == Decimal("250")


# ── the expected arrival date ────────────────────────────────────────────


@pytest.mark.anyio
async def test_earliest_arrival_wins(db_session):
    """A planner is asking "when does the next of it land", so the roll-up is a
    minimum, not a maximum or a latest."""
    late = await _po(db_session)
    await _line(db_session, late, "CR0025", "100", "0", arrival=date(2026, 9, 30))
    soon = await _po(db_session)
    await _line(db_session, soon, "CR0025", "100", "0", arrival=date(2026, 8, 20))
    result = await in_transit_by_material(db_session)
    assert result["CR0025"].earliest_arrival == date(2026, 8, 20)


@pytest.mark.anyio
async def test_the_line_date_beats_the_header_date(db_session):
    """The line date is the ERP's own; the header one was typed by a person."""
    po = await _po(db_session, eta=date(2026, 1, 1))
    await _line(db_session, po, "CR0025", "100", "0", arrival=date(2026, 9, 30))
    result = await in_transit_by_material(db_session)
    assert result["CR0025"].earliest_arrival == date(2026, 9, 30)


@pytest.mark.anyio
async def test_header_date_is_the_fallback_when_the_line_has_none(db_session):
    """UniOps-native POs have no ERP line date; the hand-entered header date is
    the only one there is, and dropping it would report "not stated" for a PO
    that states it."""
    po = await _po(db_session, eta=date(2026, 7, 15))
    await _line(db_session, po, "CR0025", "100", "0", arrival=None)
    result = await in_transit_by_material(db_session)
    assert result["CR0025"].earliest_arrival == date(2026, 7, 15)


@pytest.mark.anyio
async def test_no_date_anywhere_is_none_not_today(db_session):
    """Never guessed, never derived from a lead time. "We do not know when this
    lands" is actionable; an invented date is not."""
    po = await _po(db_session, eta=None)
    await _line(db_session, po, "CR0025", "100", "0", arrival=None)
    assert (await in_transit_by_material(db_session))["CR0025"].earliest_arrival is None


# ── the drill-down ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_open_lines_list_the_orders_behind_the_figure(db_session):
    po = await _po(db_session, number="PO-001-2510-04")
    await _line(db_session, po, "CR0025", "1000", "400", arrival=date(2026, 2, 2))
    lines = await open_lines_for_material(db_session, "CR0025")
    assert len(lines) == 1
    line = lines[0]
    assert line.po_number == "PO-001-2510-04"
    assert line.vendor_name == "Test Vendor"
    assert (line.ordered, line.received, line.remaining) == (
        Decimal("1000"), Decimal("400"), Decimal("600"))
    assert line.expected_arrival == date(2026, 2, 2)
    assert line.arrival_is_from_header is False


@pytest.mark.anyio
async def test_open_lines_say_when_the_date_came_from_the_header(db_session):
    """A planner chasing a late delivery deserves to know whether the date is
    the ERP's or somebody's estimate."""
    po = await _po(db_session, eta=date(2026, 7, 15))
    await _line(db_session, po, "CR0025", "100", "0", arrival=None)
    assert (await open_lines_for_material(db_session, "CR0025"))[0].arrival_is_from_header is True


@pytest.mark.anyio
async def test_open_lines_are_earliest_first_with_unknown_dates_last(db_session):
    """An unknown arrival date is not an early one."""
    unknown = await _po(db_session, number="PO-UNKNOWN")
    await _line(db_session, unknown, "CR0025", "100", "0", arrival=None)
    later = await _po(db_session, number="PO-LATER")
    await _line(db_session, later, "CR0025", "100", "0", arrival=date(2026, 9, 1))
    earlier = await _po(db_session, number="PO-EARLIER")
    await _line(db_session, earlier, "CR0025", "100", "0", arrival=date(2026, 8, 1))

    lines = await open_lines_for_material(db_session, "CR0025")
    assert [ln.po_number for ln in lines] == ["PO-EARLIER", "PO-LATER", "PO-UNKNOWN"]


@pytest.mark.anyio
async def test_open_lines_apply_the_same_exclusions_as_the_aggregate(db_session):
    """The drill-down and the number above it must describe the same lines —
    otherwise a planner opens a 600 kg figure and finds 2,600 kg of rows."""
    await _material(db_session, "CR0059", erp_class="0101")
    milk_po = await _po(db_session, status="nc_milk")
    await _line(db_session, milk_po, "CR0059", "1000", "0")
    unplaced = await _po(db_session, status="approved")
    await _line(db_session, unplaced, "CR0059", "1000", "0")
    assert await open_lines_for_material(db_session, "CR0059") == []


# ── the shared raw-milk definition ───────────────────────────────────────


@pytest.mark.anyio
async def test_raw_milk_codes_come_from_the_class_so_stock_and_orders_agree(db_session):
    """Exposed so WMS lots are excluded by the same definition as PO lines. The
    WMS mirror holds no raw-milk lots today — raw milk goes straight into
    production — which is exactly why the rule must be encoded rather than left
    to the fact that it currently does not matter.
    """
    await _material(db_session, "CR0010", erp_class="0101")
    await _material(db_session, "CR0059", erp_class="0101")
    await _material(db_session, "CR0025", erp_class="0102")
    await _material(db_session, "CP0133", erp_class="02")
    assert await raw_milk_material_codes(db_session) == {"CR0010", "CR0059"}
