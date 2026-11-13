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
from app.services.stock import StockLotService, StockMoveService, sync
from app.services.stock_counts import StockCountService
from tests.domain.test_transfers_edi import real_944
from tests.test_orders import OP, env  # noqa: F401 — a fixture
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
    c = await _upload(p7, when)
    assert (c.lines, c.total_ea, c.hold_ea, c.applied_at) == (4, 8000 + 7200 + 5 + 145, 5, None)
    async with p7.factory(OP) as u:
        rows = {(r.item_code, r.lot): r for r in await StockCountService(u).compare(c.id)}
    a = rows[("CF0092", "26C253125110231")]
    assert (a.received_ea, a.shipped_ea, a.sales_ea, a.radiant_ea, a.difference_ea, a.kind) == (8400, 0, 8400, 8000, 400, "differs")
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
