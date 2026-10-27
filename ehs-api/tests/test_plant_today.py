"""★ Review (uniops-8b): the server's clock is UTC, so after 8pm in Ontario
(EDT) "today" was already tomorrow — a certificate expiring today read as
expired, and anything stamped today carried tomorrow's date."""
from datetime import date, datetime, timezone

from app.services import day_range


def test_nine_pm_in_kingston_is_still_today(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            # 01:00 UTC on 28 October = 21:00 EDT on 27 October.
            return datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(day_range, "datetime", Clock)
    assert day_range.plant_today() == date(2026, 10, 27)
