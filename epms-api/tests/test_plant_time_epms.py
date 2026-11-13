"""epms-api follows the plant's time zone (Company Settings → Time Zone).

The containers run in UTC; in Toronto that is already tomorrow from 19:00 EST
/ 20:00 EDT. These pin the places in epms-api that used to read the UTC day or
clock: the daily jobs' run time, document-number dates, "today" for the
reminder sweeps, the dashboard's month, report cells and the SQL-side local
day. The zone helpers themselves are pinned in test_plant_time.py.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import literal, select

from app.core import plant_time as pt
from app.db import session as sm

TORONTO = ZoneInfo("America/Toronto")


@pytest.fixture(autouse=True)
def _toronto():
    pt.set_plant_tz("America/Toronto")
    yield
    pt.set_plant_tz("America/Toronto")


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


# ── Daily jobs: HH:MM is the plant's wall clock ─────────────────────────────

def test_daily_run_time_is_plant_time_not_utc():
    from app.tasks.daily_followup import _seconds_until_next_run
    # 18:30 EST on 11 Nov. 08:00 tomorrow in Toronto is 13:00 UTC: 13.5 h away.
    # (Read as UTC it was 08:00Z = 03:00 in Toronto, 8.5 h away.)
    assert _seconds_until_next_run(8, 0, now=_utc(2026, 11, 11, 23, 30)) == 13.5 * 3600


def test_daily_run_later_today():
    from app.tasks.daily_followup import _seconds_until_next_run
    # 07:00 EST → 08:00 EST the same day.
    assert _seconds_until_next_run(8, 0, now=_utc(2026, 11, 11, 12, 0)) == 3600


@pytest.mark.parametrize("now, hours", [
    # 08:00 EST Sat 7 Mar 2026 → 08:00 EDT Sun 8 Mar: the night is an hour short.
    (_utc(2026, 3, 7, 13, 0), 23),
    # 08:00 EDT Sat 31 Oct 2026 → 08:00 EST Sun 1 Nov: an hour long.
    (_utc(2026, 10, 31, 12, 0), 25),
])
def test_daily_run_across_dst_keeps_the_wall_clock(now, hours):
    from app.tasks.daily_followup import _seconds_until_next_run
    assert _seconds_until_next_run(8, 0, now=now) == hours * 3600


def test_daily_run_follows_the_setting():
    from app.tasks.daily_followup import _seconds_until_next_run
    pt.set_plant_tz("Asia/Shanghai")      # UTC+8, no DST
    # 23:30 UTC = 07:30 the next morning in Shanghai → 30 minutes to 08:00.
    assert _seconds_until_next_run(8, 0, now=_utc(2026, 11, 11, 23, 30)) == 1800


# ── "Today" ─────────────────────────────────────────────────────────────────

def test_local_today_is_the_setting_zone():
    from app.core.delegation import local_today
    assert local_today() == pt.plant_today()
    pt.set_plant_tz("Pacific/Kiritimati")   # UTC+14: always "ahead" of UTC
    assert local_today() == datetime.now(timezone.utc).astimezone(ZoneInfo("Pacific/Kiritimati")).date()


def test_gr_received_in_the_evening_counts_from_the_plant_day():
    from app.tasks.gr_ack_due import _received_on
    # 21:00 EST on 11 Nov = 02:00 UTC on the 12th.
    gr = SimpleNamespace(received_at=_utc(2026, 11, 12, 2, 0), created_at=None)
    assert _received_on(gr) == date(2026, 11, 11)


# ── Dashboard month ─────────────────────────────────────────────────────────

def test_dashboard_month_rolls_over_in_december():
    from app.crud.dashboard import _month_dates
    assert _month_dates(date(2026, 12, 31)) == (date(2026, 12, 1), date(2027, 1, 1))
    assert _month_dates(date(2026, 11, 11)) == (date(2026, 11, 1), date(2026, 12, 1))


# ── Report cells: ISO shape, plant wall clock ───────────────────────────────

def test_report_csv_datetime_cell_is_plant_wall_clock():
    from app.crud.reports import _dt
    # 23:30 EST on 31 Dec — UTC is already the next year.
    assert _dt(_utc(2027, 1, 1, 4, 30)) == "2026-12-31 23:30"
    assert _dt(None) == ""


# ── SQL-side local day follows the setting without a restart ────────────────

async def test_receiving_report_local_day_reads_zone_at_execution():
    from app.crud.gr_report import _local_day
    stamp = literal(_utc(2026, 11, 12, 2, 0))   # 21:00 EST on the 11th
    expr = _local_day(stamp)                     # built once, like the module-level ones
    async with sm.AsyncSessionLocal() as db:
        assert (await db.execute(select(expr))).scalar_one() == date(2026, 11, 11)
        pt.set_plant_tz("Asia/Shanghai")
        assert (await db.execute(select(expr))).scalar_one() == date(2026, 11, 12)


# ── Document numbers take the plant's date ──────────────────────────────────

# 21:00 EST on 31 Dec 2031 = 02:00 UTC on 1 Jan 2032: day, month and year all
# differ between the two clocks.
_NYE = datetime(2031, 12, 31, 21, 0, tzinfo=TORONTO)


async def test_document_numbers_use_the_plant_date(monkeypatch):
    from app.crud import agreement as agreement_crud
    from app.crud import gr as gr_crud
    from app.crud import invoice as invoice_crud
    from app.crud import pa as pa_crud
    from app.crud import po as po_crud
    from app.crud import pr as pr_crud
    from app.admin import po_number as admin_po_number
    for mod in (pr_crud, pa_crud, gr_crud, po_crud, agreement_crud, invoice_crud, admin_po_number):
        monkeypatch.setattr(mod, "plant_now", lambda: _NYE)
    async with sm.AsyncSessionLocal() as db:
        assert (await pr_crud._next_number(db)).startswith("PR-20311231-")
        assert (await pa_crud._next_number(db)).startswith("PA-20311231-")
        assert (await gr_crud._next_number(db)).startswith("GR-20311231-")
        assert (await agreement_crud._next_number(db)).startswith("AGR-203112-")
        assert (await po_crud._next_number(db, "332")).startswith("PO-332-3112-")
        assert (await admin_po_number._next_number(db, "332")).startswith("PO-332-3112-")
        assert (await invoice_crud._next_ref(db)).startswith("INV-2031-")
        await db.rollback()


async def test_number_prefix_already_used_in_the_utc_era_does_not_collide(monkeypatch):
    """Before the switch, an evening PR took tomorrow's UTC date. The sequence
    is max(existing tail) + 1 per prefix, so the plant day's prefix and the
    UTC-era "tomorrow" prefix both keep counting without a duplicate."""
    from app.crud import pr as pr_crud
    from app.crud import user as user_crud
    from app.models.pr import PurchaseRequest
    from app.schemas.auth import RegisterRequest

    async with sm.AsyncSessionLocal() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"tz-num-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="TZ Numbering", role="requester"))
        # 20:30 EST on 2031-03-04 under the old rule → PR-20310305-0003 (UTC day).
        for number in ("PR-20310304-0007", "PR-20310305-0003"):
            db.add(PurchaseRequest(number=number, title="tz", type=1, created_by=user.id))
        await db.flush()

        evening = datetime(2031, 3, 4, 21, 0, tzinfo=TORONTO)   # 02:00 UTC on the 5th
        monkeypatch.setattr(pr_crud, "plant_now", lambda: evening)
        assert await pr_crud._next_number(db) == "PR-20310304-0008"

        morning = evening + timedelta(hours=12)                 # 09:00 on the 5th
        monkeypatch.setattr(pr_crud, "plant_now", lambda: morning)
        assert await pr_crud._next_number(db) == "PR-20310305-0004"
        await db.rollback()


# ── Human-facing text ───────────────────────────────────────────────────────

def test_digest_created_column_is_plant_day():
    from app.tasks.daily_followup import _Inbox, _digest_html
    task = SimpleNamespace(
        created_at=_utc(2026, 11, 12, 2, 0), type="approve_pr", document_type="pr",
        document_id=uuid.uuid4(), document_number="PR-1", title="t")
    body = _digest_html(_Inbox("a@b.c", "A", None, [task]), _utc(2026, 11, 12, 3, 0))
    assert "2026-NOV-11" in body
    assert "2026-11-12" not in body
