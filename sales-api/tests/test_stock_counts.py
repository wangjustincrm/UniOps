"""Radiant's inventory report as a count (user 2026-11-13): read the portal's file, compare it lot by lot with
Sales' moves, apply it as the start (records kept), undo it."""
import io
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.domain.radiant_stock import StockFileError, parse_stock_file
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.query import QuerySpec
from app.services.stock import StockItemService, StockLotService, StockMoveService, sync
from app.services.stock_counts import StockCountService
from tests.domain.test_transfers_edi import real_944
from app.domain.status import Fulfillment as F
from app.models.orders import Order
from tests.test_invoicing import p6  # noqa: F401 — a fixture
from tests.test_orders import OP, env, po, run_850  # noqa: F401 — a fixture
from tests.test_shipments import store_945, x945
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import p7, sent_transfer, store  # noqa: F401 — p7 is a fixture

SUMMARY = (
    "Wh Id,Client Code,Display Item Number,Upc,Description,Lot Number,Expiration Date,Total Quantity,Uom,Status\n"
    "CLN02,CRM,CF0092,628347940140,NIURISS STAGE 1,26C253125110231,2028-09-08,8000,EA,AVAILABLE\n"
    "CLN02,CRM,CF0103,628347940362,NIURISS SMART 2,26E248121010211,2028-09-03,7200,EA,AVAILABLE\n"
    "CLN02,CRM,CF0110,,WALMART PARENTS CHOICE,25C26112511032,2027-09-16,5,EA,ON-HOLD\n"
    "CLN02,CRM,CHEP,,CHEP,,1900-01-01,145,EA,AVAILABLE\n"
)


def test_the_summary_report_reads_by_item_and_lot():
    rows = {(r.item_code, r.lot): r for r in parse_stock_file("inv.csv", SUMMARY.encode())}
    assert rows[("CF0092", "26C253125110231")].qty_ea == 8000
    assert rows[("CF0092", "26C253125110231")].expiry == date(2028, 9, 8)
    assert rows[("CF0110", "25C26112511032")].hold_ea == 5
    assert rows[("CHEP", None)].qty_ea == 145 and rows[("CHEP", None)].expiry is None  # 1900-01-01 is "none"


def test_the_pallet_detail_adds_up_per_lot_and_takes_the_client_prefix_off():
    detail = ("Warehouse ID,Client Code,Item Number,Display Item Number,Lot Number,Expiration Date,Actual Quantity,Status,Current Holds\n"
              "CLN02,CRM,CRM|CF0092,CF0092,L1,2028-09-08,600,A,\n"
              "CLN02,CRM,CRM|CF0092,CF0092,L1,2028-09-08,310,A,\n"
              "CLN02,CRM,CRM|CF0110,,L2,2027-09-16,4,H,Holds(1)\n")
    rows = {(r.item_code, r.lot): r for r in parse_stock_file("lp.csv", detail.encode())}
    assert rows[("CF0092", "L1")].qty_ea == 910 and rows[("CF0092", "L1")].hold_ea == 0
    assert rows[("CF0110", "L2")].qty_ea == 4 and rows[("CF0110", "L2")].hold_ea == 4  # display empty: Item Number, no CRM|


def _xlsx(rows: list[list[object]]) -> bytes:
    def cell(ref, v):
        return f'<c r="{ref}"><v>{v}</v></c>' if isinstance(v, (int, float)) else f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'
    body = "".join(f'<row r="{i}">' + "".join(cell(f"{chr(65 + j)}{i}", v) for j, v in enumerate(r)) + "</row>" for i, r in enumerate(rows, 1))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   f"<sheetData>{body}</sheetData></worksheet>")
    return buf.getvalue()


def test_an_excel_report_is_read_with_excel_dates():
    data = _xlsx([["Display Item Number", "Lot Number", "Expiration Date", "Total Quantity", "Uom", "Status"],
                  ["CF0093", "26E108131010121", (date(2027, 4, 16) - date(1899, 12, 30)).days, 702, "EA", "AVAILABLE"]])  # Excel days
    [r] = parse_stock_file("inv.xlsx", data)
    assert (r.item_code, r.lot, r.qty_ea, r.expiry) == ("CF0093", "26E108131010121", 702, date(2027, 4, 16))


@pytest.mark.parametrize("text, says", [
    ("a,b\n1,2\n", "Lot Number"),
    ("Display Item Number,Lot Number,Total Quantity,Uom\nCF0092,L1,10,CS\n", "unit CS"),
    ("Display Item Number,Lot Number,Total Quantity\nCF0092,L1,1.5\n", "whole number"),
])
def test_a_file_that_is_not_the_report_says_why(text, says):
    with pytest.raises(StockFileError, match=says):
        parse_stock_file("x.csv", text.encode())


@pytest.fixture(autouse=True)
async def _empty(db_engine):
    async with db_engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE sales_stock_moves, sales_stock_count_lines, sales_stock_counts RESTART IDENTITY CASCADE"))


async def _sync(t):
    async with t.factory(SYSTEM_ACTOR) as u:
        out = await sync(u)
        await u.commit()
    return out


async def _lots(t):
    async with t.factory(OP) as u:
        return {(x.item_code, x.lot): x.on_hand_ea for x in (await StockLotService(u).list(QuerySpec(page_size=200))).items}


async def _received(t):
    """00104 sent and its real 944 in: CF0092 26C253125110231 8400 and CF0103 26E248121010211 7200 at Radiant."""
    await sent_transfer(t)
    await store(t, "944_EDI", real_944(), "944-16468.edi")
    await t.tr.process_944s()
    await _sync(t)


async def _upload(t, when):
    async with t.factory(OP) as u:
        c = await StockCountService(u).create(name="inventory.csv", data=SUMMARY.encode(), counted_at=when, note="portal")
        await u.commit()
    return c


async def test_the_report_compares_each_lot_and_apply_starts_from_radiant(p7):  # noqa: F811
    await _received(p7)
    when = datetime.now(timezone.utc) - timedelta(minutes=1)
    async with p7.factory(SYSTEM_ACTOR) as u:  # an old lot: 30 received long ago, never shipped in Sales' records
        await u.session.execute(sa.text("""INSERT INTO sales_stock_moves (at, item_code, lot, qty_ea, from_place, to_place, source_type, source_key)
                                           VALUES (:at, 'CF0092', 'OLD1', 30, 'TO_RADIANT', 'RADIANT', 'receipt_row', 'old-1')"""),
                                {"at": when - timedelta(days=300)})
        await u.commit()
    c = await _upload(p7, when)
    assert (c.lines, c.total_ea, c.hold_ea, c.applied_at) == (4, 8000 + 7200 + 5 + 145, 5, None)
    async with p7.factory(OP) as u:
        rows = {(r.item_code, r.lot): r for r in await StockCountService(u).compare(c.id)}
    a = rows[("CF0092", "26C253125110231")]
    assert (a.received_ea, a.shipped_ea, a.sales_ea, a.radiant_ea, a.difference_ea, a.kind) == (8400, 0, 8400, 8000, 400, "differs")
    assert (a.in_ea, a.in_after_ea, a.out_after_ea, a.now_ea) == (8400, 0, 0, 8000)
    hold = rows[("CF0110", "25C26112511032")]
    assert (hold.now_ea, hold.hold_ea, hold.available_ea, hold.open) == (5, 5, 0, True)  # on hold: in the total, not available
    old = rows[("CF0092", "OLD1")]  # an old lot Sales still holds 30 of, Radiant none: closed, in no total
    assert (old.sales_ea, old.radiant_ea, old.now_ea, old.kind, old.open) == (30, 0, 0, "sales_only", False)
    assert rows[("CF0092", "26C253125110231")].open
    assert rows[("CF0103", "26E248121010211")].kind == "match"
    assert rows[("CHEP", None)].kind == "radiant_only" and rows[("CF0110", "25C26112511032")].hold_ea == 5
    before = await _lots(p7)
    assert before[("CF0092", "26C253125110231")] == 8400 and ("CHEP", None) not in before  # reading changes nothing

    async with p7.factory(OP) as u:
        c = await StockCountService(u).apply(c.id, version=c.version)
        await u.commit()
    lots = await _lots(p7)
    assert lots[("CF0092", "26C253125110231")] == 8000 and lots[("CF0103", "26E248121010211")] == 7200
    assert lots[("CHEP", None)] == 145
    assert await _sync(p7) == {}  # applied once: a second sync adds nothing
    async with p7.factory(OP) as u:
        lot = {(x.item_code, x.lot): x for x in (await StockLotService(u).list(QuerySpec(page_size=200))).items}
        items = {x.item_code: x for x in (await StockItemService(u).list(QuerySpec(page_size=200))).items}
    a = lot[("CF0092", "26C253125110231")]
    # Applied, the lot starts from Radiant's figure: received = 8000 (the report) + what came after; nothing adjusted.
    assert (a.received_ea, a.returned_ea, a.shipped_ea, a.adjusted_ea, a.on_hand_ea, a.hold_ea, a.available_ea) == (8000, 0, 0, 0, 8000, 0, 8000)
    assert a.report_diff_ea == 400  # Sales had 8400, Radiant 8000: kept in sight
    assert lot[("CF0103", "26E248121010211")].expiry == date(2028, 9, 3)  # no 945 named it: Radiant's report does
    h = lot[("CF0110", "25C26112511032")]  # on hold at Radiant: on hand, not available
    assert (h.on_hand_ea, h.hold_ea, h.available_ea) == (5, 5, 0)
    assert lot[("CF0092", "OLD1")].on_hand_ea == 0  # closed by the count
    # The item adds up its open lots only: OLD1 (closed by the report) is in none of CF0092's figures.
    assert (items["CF0092"].received_ea, items["CF0092"].shipped_ea, items["CF0092"].on_hand_ea) == (8000, 0, 8000)
    assert items["CF0092"].report_diff_ea == 400  # its open lots only (OLD1's 30 is closed)
    assert items["CF0092"].available_ea == items["CF0092"].on_hand_ea - items["CF0092"].hold_ea - items["CF0092"].allocated_ea

    # Kept right: a move dated before the count, arriving later, changes the count's move, not the figure;
    # a move after it adds up.
    async with p7.factory(OP) as u:
        await StockMoveService(u).adjust(item_code="CF0092", lot="26C253125110231", qty_ea=-30, reason="damaged, after the count")
        await u.commit()
    async with p7.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text("""INSERT INTO sales_stock_moves (at, item_code, lot, qty_ea, from_place, to_place, source_type, source_key)
                                      VALUES (:at, 'CF0092', '26C253125110231', 100, 'RADIANT', 'CUSTOMER', 'shipment_row', 'late-945')"""),
                                {"at": when - timedelta(hours=2)})
        await u.commit()
    assert (await _sync(p7)).get("count") == 1
    assert (await _lots(p7))[("CF0092", "26C253125110231")] == 8000 - 30
    async with p7.factory(OP) as u:
        a = {(r.item_code, r.lot): r for r in await StockCountService(u).compare(c.id)}[("CF0092", "26C253125110231")]
    assert (a.out_after_ea, a.in_after_ea, a.now_ea) == (30, 0, 8000 - 30)  # remaining now = Radiant − out after

    async with p7.factory(OP) as u:
        with pytest.raises(BusinessError):
            await StockCountService(u).update(c.id, version=c.version, counted_at=when - timedelta(days=1), note=None)
        c = await StockCountService(u).undo(c.id, version=c.version, reason="wrong moment")
        await u.commit()
    assert c.applied_at is None
    lots = await _lots(p7)
    assert lots[("CF0092", "26C253125110231")] == 8400 - 100 - 30 and ("CHEP", None) not in lots


async def test_a_report_from_the_future_is_refused(p7):  # noqa: F811
    async with p7.factory(OP) as u:
        with pytest.raises(BusinessError, match="future"):
            await StockCountService(u).create(name="i.csv", data=SUMMARY.encode(), counted_at=datetime.now(timezone.utc) + timedelta(days=1), note=None)


async def test_a_return_comes_off_shipped(p7):  # noqa: F811
    """User 2026-11-13: shipped 100, the customer sent 10 back (RA 944) → shipped 90, on hand received − 90."""
    async with p7.factory(SYSTEM_ACTOR) as u:
        for day, frm, to, q, src, key in ((1, "TO_RADIANT", "RADIANT", 500, "receipt_row", "r1"),
                                         (2, "RADIANT", "TO_CUSTOMER", 100, "shipment_row", "s1"),
                                         (9, "RETURNING", "RADIANT", 10, "return_944", "ra1")):
            await u.session.execute(sa.text("""INSERT INTO sales_stock_moves (at, item_code, lot, qty_ea, from_place, to_place, source_type, source_key)
                                               VALUES (:at, 'CF0092', 'RET1', :q, :f, :t, :src, :key)"""),
                                    {"at": datetime(2026, 10, day, 12, tzinfo=timezone.utc), "q": q, "f": frm, "t": to, "src": src, "key": key})
        await u.commit()
    async with p7.factory(OP) as u:
        [lot] = [x for x in (await StockLotService(u).list(QuerySpec(page_size=200))).items if x.lot == "RET1"]
    assert (lot.received_ea, lot.returned_ea, lot.shipped_ea, lot.adjusted_ea, lot.on_hand_ea, lot.available_ea) == (500, 10, 90, 0, 410, 410)


def test_the_allocation_column_is_read_as_picked_for_an_order():
    detail = ("Display Item Number,Lot Number,Actual Quantity,Status,Allocation\n"
              "CF0092,L1,400,A,Storage\nCF0092,L1,600,A,CRM|001008724\nCF0092,L1,100,A,CRM|001008724\n")
    [r] = parse_stock_file("lp.csv", detail.encode())
    assert (r.qty_ea, r.picked) == (1100, {"1008724": 700})


async def test_a_lot_s_picked_pallets_are_in_process_until_the_945(p6):  # noqa: F811
    """User 2026-11-13: Radiant's report shows a pallet picked for an order (Allocation CRM|<order>); until its 945,
    that lot has it in process (not available); with the 945 it is shipped."""
    await run_850(p6.edi.env, {"PurchaseOrder": [po("LBL", "PO-PICK", [{"Line": 1, "BuyerPart": "L-555", "Quantity": 10, "UOM": "CA",
                                                                         "UnitPrice": "50"}], ship_to_gln="0012345000011")]})
    o, _ = await p6.edi.env.order("PO-PICK")
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, o.id)).fulfillment_status = F.ACCEPTED_940.value
        await u.commit()
    report = ("Display Item Number,Lot Number,Actual Quantity,Status,Allocation\n"
              f"CF0092,LOT-1,940,A,Storage\nCF0092,LOT-1,60,A,CRM|{o.order_number:09d}\n")
    async with p6.factory(OP) as u:
        c = await StockCountService(u).create(name="r.csv", data=report.encode(), counted_at=datetime(2026, 11, 1, 12, tzinfo=timezone.utc), note=None)
        await StockCountService(u).apply(c.id, version=c.version)
        await u.commit()

    async def lot():
        async with p6.factory(OP) as u:
            return next(x for x in (await StockLotService(u).list(QuerySpec(page_size=200))).items if x.lot == "LOT-1")
    a = await lot()
    assert (a.received_ea, a.shipped_ea, a.on_hand_ea, a.in_process_ea, a.available_ea) == (1000, 0, 1000, 60, 940)
    await store_945(p6, x945(f"{o.order_number:09d}", ["LX*1", "W12*CP*60*60*0*EA**VN*CF0092", "N9*LT*LOT-1"],
                            header=["DTM*011*20261102"], ctl=f"{900000000 + o.id}"), "945-pick.edi")
    await p6.ships.process_945s()
    await _sync(p6)
    a = await lot()
    assert (a.received_ea, a.shipped_ea, a.on_hand_ea, a.in_process_ea, a.available_ea) == (1000, 60, 940, 0, 940)
