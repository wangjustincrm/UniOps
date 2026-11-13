"""Document numbers and "today" follow the plant time zone (Company Settings),
not UTC.

At 20:30 EST on 2030-01-15 it is already 2030-01-16 in UTC. The claim number
must carry the plant's date, and since the allocator takes max tail + 1 under
the prefix, a number minted earlier for the same prefix (e.g. under the old
UTC rule) is continued past, never reissued.
"""
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

from app.core import plant_time
from app.core.delegation import local_today
from app.crud import expense as expense_crud
from app.schemas.expense import ExpenseClaimCreate

# 20:30 EST, Jan 15 2030 at the plant == 01:30 UTC, Jan 16 2030.
_EVENING = datetime(2030, 1, 16, 1, 30, tzinfo=timezone.utc)


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return _EVENING.astimezone(tz) if tz else _EVENING.replace(tzinfo=None)


@pytest.fixture
def toronto_evening(monkeypatch):
    plant_time.set_plant_tz("America/Toronto")
    monkeypatch.setattr(plant_time, "datetime", _Clock)
    yield
    plant_time.set_plant_tz("America/Toronto")


def test_today_is_the_plants_date_in_the_evening(toronto_evening):
    assert plant_time.plant_today() == date(2030, 1, 15)
    assert local_today() == date(2030, 1, 15)   # delegation windows use it


def test_today_follows_the_setting(toronto_evening):
    plant_time.set_plant_tz("Asia/Shanghai")    # 09:30 on Jan 16 there
    assert local_today() == date(2030, 1, 16)


async def test_claim_number_carries_the_plant_date_and_continues_past_taken_numbers(
        db_session, toronto_evening):
    data = ExpenseClaimCreate(claim_type="MIL", submission_date=date(2030, 1, 15), currency="CAD")
    args = dict(user_id=uuid.uuid4(), user_name="Emp", department_id=None, department_name="Ops")

    c1 = await expense_crud.create_claim(db_session, data, **args)
    assert c1.claim_number.startswith("MIL-20300115-"), c1.claim_number

    # The prefix already holds a higher number (minted elsewhere / earlier).
    await db_session.execute(
        text("UPDATE expense_claims SET claim_number = :n WHERE id = :i"),
        {"n": "MIL-20300115-0007", "i": c1.id},
    )
    await db_session.flush()

    c2 = await expense_crud.create_claim(db_session, data, **args)
    assert c2.claim_number == "MIL-20300115-0008"
    await db_session.rollback()
