"""Days to expiry count from the Toronto day (user 2026-11-24), as a stock count's do — not from the database session's
(UTC) day, which is the next one from 19:00 / 20:00 Toronto. Nothing here depends on the hour the suite runs: the
session's zone is set 26 hours apart (UTC+14 and UTC-12), so its own day cannot agree with Toronto's in both."""
from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.lots import LotRow
from app.models.stock import RADIANT, StockItem, StockLot, StockMove
from app.platform.clock import FixedClock, business_date_sql
from app.platform.query import Filter, QuerySpec
from app.services.lots import LotService
from app.services.stock import EXPIRING_DAYS, StockLotService
from tests.test_invoicing import p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture

UTC = timezone.utc
FAR_APART = ("Pacific/Kiritimati", "Etc/GMT+12")  # UTC+14, UTC-12


def sql(e) -> str:
    return str(e.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.parametrize(("model", "column"), [(StockLot, "days_to_expiry"), (StockItem, "days_to_first_expiry"), (LotRow, "days_to_expiry")])
def test_the_lists_count_from_the_business_day(model, column):
    text = sql(model.__table__.element)
    assert column in text and sql(business_date_sql()) in text and "current_date" not in text.lower()


@pytest.mark.parametrize(("at", "toronto", "utc"), [
    (datetime(2026, 7, 15, 1, 30, tzinfo=UTC), date(2026, 7, 14), date(2026, 7, 15)),   # EDT
    (datetime(2026, 1, 15, 4, 30, tzinfo=UTC), date(2026, 1, 14), date(2026, 1, 15))])  # EST
async def test_sql_and_python_agree_where_utc_is_already_tomorrow(env, at, toronto, utc):  # noqa: F811
    day = business_date_sql(sa.literal(at, sa.DateTime(timezone=True)))
    expiry = sa.literal(date(2026, 12, 31), sa.Date)
    async with env.factory(OP) as u:
        got, days = (await u.session.execute(sa.select(day, expiry - day))).one()
    assert got == toronto == FixedClock(at).today() and got != utc
    assert days == (date(2026, 12, 31) - toronto).days


async def test_the_days_do_not_follow_the_session_s_zone_and_the_expiring_views_keep_their_bounds(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-DAYS")
    async with p6.factory(OP) as u:
        s = u.session
        today = (await s.execute(sa.select(business_date_sql()))).scalar_one()  # one transaction: one now()
        edge = {"D-1": -1, "D0": 0, "D90": EXPIRING_DAYS, "D91": EXPIRING_DAYS + 1}
        for lot, n in edge.items():
            s.add(StockMove(at=datetime.now(UTC), item_code="CF0092", lot=lot, expiry=today + timedelta(days=n), qty_ea=5,
                            from_place="PLANT", to_place=RADIANT, source_type="test", source_key=f"days-{lot}"))
        row_id = (await s.execute(sa.select(sa.func.min(LotRow.id)).where(LotRow.order_id == o.id))).scalar_one()
        await s.execute(sa.text("UPDATE sales_shipment_rows SET expiry = :d WHERE id = :i"), {"d": today, "i": row_id})
        await s.flush()
        for zone in FAR_APART:
            await s.execute(sa.text(f"SET LOCAL TIME ZONE '{zone}'"))
            lots = dict((await s.execute(sa.select(StockLot.lot, StockLot.days_to_expiry).where(StockLot.lot.in_(edge)))).all())
            assert lots == edge, zone
            first = (await s.execute(sa.select(StockItem.first_expiry, StockItem.days_to_first_expiry)
                                     .where(StockItem.item_code == "CF0092"))).one()
            assert first == (today - timedelta(days=1), -1), zone
            assert (await s.execute(sa.select(LotRow.days_to_expiry).where(LotRow.id == row_id))).scalar_one() == 0, zone
            # the saved "Expiring soon" views: 0 and 90 days are in, -1 and 91 are out
            f = [Filter(field="days_to_expiry", op="between", value=[0, EXPIRING_DAYS])]
            page = await StockLotService(u).list(QuerySpec(page_size=200, filters=f))
            assert {x.lot for x in page.items} & set(edge) == {"D0", "D90"}, zone
            page = await LotService(u).list(QuerySpec(page_size=200, filters=f))
            assert row_id in {x.id for x in page.items}, zone
        await u.rollback()
