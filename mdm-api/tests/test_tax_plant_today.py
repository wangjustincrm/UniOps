"""With no as_of, the tax rate in force is the one on the plant's date (Company
Settings time zone), not the UTC date: at 20:30 EST on Dec 31 it is already
Jan 1 in UTC, and a rate starting Jan 1 must not apply yet."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.core import plant_time
from app.crud import tax as tax_crud
from app.schemas.tax import TaxCodeCreate

# 20:30 EST, Dec 31 2029 at the plant == 01:30 UTC, Jan 1 2030.
_EVENING = datetime(2030, 1, 1, 1, 30, tzinfo=timezone.utc)


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return _EVENING.astimezone(tz) if tz else _EVENING.replace(tzinfo=None)


@pytest.fixture
def new_years_eve(monkeypatch):
    plant_time.set_plant_tz("America/Toronto")
    monkeypatch.setattr(plant_time, "datetime", _Clock)
    yield
    plant_time.set_plant_tz("America/Toronto")


def _code(rate, frm, to=None):
    return TaxCodeCreate(code="HST_ON", name=f"HST {rate}", tax_type="HST", province="ON",
                         rate=Decimal(rate), recoverable=True, effective_from=frm,
                         effective_to=to, active=True)


async def test_default_as_of_is_the_plants_date(db_session, new_years_eve):
    await tax_crud.create_code(db_session, _code("0.13", date(2024, 1, 1), date(2029, 12, 31)))
    await tax_crud.create_code(db_session, _code("0.15", date(2030, 1, 1)))
    rates = [c.rate for c in await tax_crud.list_codes(db_session) if c.code == "HST_ON"]
    assert rates == [Decimal("0.13")]
