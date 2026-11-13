"""Delegation windows (代班) end on the plant's date — the Company Settings
time zone — not the UTC date and not a hard-coded zone."""
from datetime import date, datetime, timezone

import pytest

from app.core import plant_time
from app.crud.delegation import local_today

# 20:30 EST, Jan 15 2030 at the plant == 01:30 UTC, Jan 16 2030.
_EVENING = datetime(2030, 1, 16, 1, 30, tzinfo=timezone.utc)


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return _EVENING.astimezone(tz) if tz else _EVENING.replace(tzinfo=None)


@pytest.fixture
def evening(monkeypatch):
    plant_time.set_plant_tz("America/Toronto")
    monkeypatch.setattr(plant_time, "datetime", _Clock)
    yield
    plant_time.set_plant_tz("America/Toronto")


def test_evening_in_toronto_is_still_today(evening):
    # A window ending Jan 15 must still be active at 20:30 that evening.
    assert local_today() == date(2030, 1, 15)


def test_follows_the_company_setting(evening):
    plant_time.set_plant_tz("Asia/Shanghai")
    assert local_today() == date(2030, 1, 16)


def test_an_invalid_setting_keeps_the_current_zone(evening):
    assert plant_time.set_plant_tz("Mars/Olympus") is False
    assert plant_time.set_plant_tz("UTC") is False   # legacy alias, not a plant zone
    assert local_today() == date(2030, 1, 15)
