"""The plant's time zone as a company setting (UI-DATE-002, Company Settings).

plant_time is a sibling copy in every *-api; these tests pin its behaviour
once, here.
"""
from datetime import date, datetime, timezone

import pytest

from app.core import plant_time as pt

CONFIG_URL = "/api/v1/config"
BRANDING_URL = "/api/v1/config/public/branding"


@pytest.fixture(autouse=True)
def _toronto():
    pt.set_plant_tz("America/Toronto")
    yield
    pt.set_plant_tz("America/Toronto")


def test_evening_in_toronto_is_still_today():
    # 21:30 EST on 11 Nov = 02:30 UTC on 12 Nov.
    utc = datetime(2026, 11, 12, 2, 30, tzinfo=timezone.utc)
    assert pt.plant_today(utc) == date(2026, 11, 11)
    assert pt.fmt_date(utc) == "2026-NOV-11"
    assert pt.fmt_datetime(utc) == "2026-NOV-11 21:30"
    assert pt.fmt_datetime(utc, with_zone=True) == "2026-NOV-11 21:30 EST"


def test_daylight_saving_is_followed_not_a_fixed_offset():
    summer = datetime(2026, 7, 4, 16, 5, tzinfo=timezone.utc)
    assert pt.fmt_datetime(summer, with_zone=True) == "2026-JUL-04 12:05 EDT"


def test_naive_datetime_is_taken_as_utc():
    assert pt.fmt_datetime(datetime(2026, 1, 1, 3, 0)) == "2025-DEC-31 22:00"


def test_calendar_date_is_printed_as_is():
    assert pt.fmt_date(date(2026, 1, 1)) == "2026-JAN-01"
    assert pt.fmt_date(None) == "—"
    assert pt.fmt_datetime(None, empty="") == ""


def test_day_and_month_bounds_are_local_midnight():
    start, end = pt.plant_day_bounds(date(2026, 11, 1))   # DST ends that day
    assert start.astimezone(timezone.utc) == datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
    assert end.astimezone(timezone.utc) == datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)
    # Subtract in UTC: two aware datetimes in the same zone subtract by wall clock.
    assert (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() == 25 * 3600
    assert pt.plant_month_start(date(2026, 11, 30)) == start


def test_setting_another_zone_moves_everything():
    assert pt.set_plant_tz("America/Regina")      # UTC-6 all year, no DST
    utc = datetime(2026, 11, 12, 2, 30, tzinfo=timezone.utc)
    assert pt.fmt_datetime(utc) == "2026-NOV-11 20:30"
    assert pt.plant_tz_name() == "America/Regina"


def test_invalid_zone_is_refused_and_current_kept():
    # Offsets and legacy aliases are not zone names: no DST, or a reversed
    # sign (Etc/GMT+5 is UTC-5).
    for bad in ("UTC-5", "", "Etc/GMT+5", "EST", "EST5EDT", "UTC", "Not/AZone"):
        assert not pt.set_plant_tz(bad), bad
    assert "America/Toronto" in pt.timezone_choices()
    assert not any(z.startswith("Etc/") for z in pt.timezone_choices())
    assert pt.plant_tz_name() == "America/Toronto"


@pytest.mark.asyncio
async def test_timezone_setting_round_trips_and_reaches_branding(admin_client, client):
    r = await admin_client.get(CONFIG_URL)
    assert r.status_code == 200
    assert r.json()["timezone"] == "America/Toronto"

    r = await admin_client.patch(CONFIG_URL, json={"timezone": "America/Regina"})
    assert r.status_code == 200, r.text
    assert r.json()["timezone"] == "America/Regina"
    assert pt.plant_tz_name() == "America/Regina"   # this process at once

    r = await client.get(BRANDING_URL)
    assert r.status_code == 200
    assert r.json()["timezone"] == "America/Regina"

    await admin_client.patch(CONFIG_URL, json={"timezone": "America/Toronto"})


@pytest.mark.asyncio
async def test_offset_instead_of_zone_name_is_rejected(admin_client):
    for bad in ("UTC-5", "Etc/GMT+5", "EST"):
        r = await admin_client.patch(CONFIG_URL, json={"timezone": bad})
        assert r.status_code == 422, bad
    r = await admin_client.get(CONFIG_URL)
    assert r.json()["timezone"] == "America/Toronto"


@pytest.mark.asyncio
async def test_refresh_reads_the_shared_row(admin_client, test_engine):
    await admin_client.patch(CONFIG_URL, json={"timezone": "Europe/London"})
    pt.set_plant_tz("America/Toronto")          # as another service would be
    await pt.refresh_plant_tz(test_engine)
    assert pt.plant_tz_name() == "Europe/London"
    await admin_client.patch(CONFIG_URL, json={"timezone": "America/Toronto"})
