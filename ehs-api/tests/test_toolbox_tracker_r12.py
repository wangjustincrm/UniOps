"""Round 12, TBT-01 (HSE tracks each person's toolbox talks month by month)
and TBT-02 (chasing outstanding ones by hand, one or all)."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.communication import Communication
from app.models.toolbox import TbtDelivery, ToolboxTalk
from app.services.day_range import plant_today
from tests.conftest import make_user


def _month(offset: int = 0) -> date:
    first = plant_today().replace(day=1)
    if offset < 0:
        return (first - timedelta(days=1)).replace(day=1)
    return first


@pytest.fixture
async def month_of_talks(test_engine, db_session):
    """Two talks this month, one last month; Ken owes one, Anna has done both."""
    ken = await make_user(test_engine, role="supervisor", full_name="Ken Boucher")
    anna = await make_user(test_engine, role="supervisor", full_name="Anna Lee")
    this, last = _month(), _month(-1)
    t1 = ToolboxTalk(id=uuid.uuid4(), talk_no=f"TBT-{uuid.uuid4().hex[:5]}",
                     title="Ladders", period_month=this, is_published=True)
    t2 = ToolboxTalk(id=uuid.uuid4(), talk_no=f"TBT-{uuid.uuid4().hex[:5]}",
                     title="Lock-out", period_month=this, is_published=True)
    t0 = ToolboxTalk(id=uuid.uuid4(), talk_no=f"TBT-{uuid.uuid4().hex[:5]}",
                     title="Last month's", period_month=last, is_published=True)
    db_session.add_all([t1, t2, t0])
    await db_session.flush()

    def d(talk, who, status):
        return TbtDelivery(id=uuid.uuid4(), talk_id=talk.id, supervisor_id=who.id,
                           supervisor_name=who.full_name, department_name="Packaging",
                           status=status,
                           held_at=datetime.now(timezone.utc) if status == "delivered" else None)
    owed = d(t1, ken, "pending")
    db_session.add_all([owed, d(t2, ken, "delivered"),
                        d(t1, anna, "delivered"), d(t2, anna, "delivered"),
                        d(t0, ken, "pending")])
    await db_session.commit()
    return {"ken": ken, "anna": anna, "owed": owed}


async def test_the_tracker_shows_each_person_for_the_month(hse_manager, month_of_talks):
    _, client = hse_manager
    page = await client.get(f"/api/v1/toolbox-talks/tracker?month={_month().isoformat()}")
    assert page.status_code == 200, page.text
    body = page.json()
    rows = {r["person_name"]: r for r in body["items"]}
    assert rows["Ken Boucher"]["assigned"] == 2
    assert rows["Ken Boucher"]["delivered"] == 1 and rows["Ken Boucher"]["outstanding"] == 1
    assert rows["Anna Lee"]["outstanding"] == 0
    # Last month's talk is last month's.
    assert {x["title"] for x in rows["Ken Boucher"]["deliveries"]} == {"Ladders", "Lock-out"}
    assert (body["assigned"], body["delivered"], body["outstanding"]) == (4, 3, 1)
    # Who still owes one comes first by default.
    assert body["items"][0]["person_name"] == "Ken Boucher"

    by_name = (await client.get(
        f"/api/v1/toolbox-talks/tracker?month={_month().isoformat()}"
        "&sort=person_name&dir=asc")).json()["items"]
    assert [r["person_name"] for r in by_name] == ["Anna Lee", "Ken Boucher"]
    searched = (await client.get(
        f"/api/v1/toolbox-talks/tracker?month={_month().isoformat()}&q=ken")).json()
    assert searched["total"] == 1
    paged = (await client.get(
        f"/api/v1/toolbox-talks/tracker?month={_month().isoformat()}&page_size=1")).json()
    assert paged["total"] == 2 and len(paged["items"]) == 1
    assert (await client.get(
        f"/api/v1/toolbox-talks/tracker?month={_month().isoformat()}&sort=bogus")).status_code == 422


async def test_the_tracker_and_the_chase_are_hses(worker, month_of_talks):
    _, client = worker
    assert (await client.get(
        f"/api/v1/toolbox-talks/tracker?month={_month().isoformat()}")).status_code == 403
    assert (await client.post(
        f"/api/v1/toolbox-talks/deliveries/{month_of_talks['owed'].id}/remind")).status_code == 403
    assert (await client.post(
        f"/api/v1/toolbox-talks/remind-all?month={_month().isoformat()}")).status_code == 403


async def _chases(db_session, delivery_id) -> int:
    return len((await db_session.execute(select(Communication).where(
        Communication.doc_id == delivery_id,
        Communication.template_key == "toolbox_chase"))).scalars().all())


async def test_one_outstanding_talk_can_be_chased_now(hse_manager, month_of_talks, db_session):
    _, client = hse_manager
    owed = month_of_talks["owed"]
    sent = await client.post(f"/api/v1/toolbox-talks/deliveries/{owed.id}/remind")
    assert sent.status_code == 200, sent.text
    assert await _chases(db_session, owed.id) == 1
    await db_session.refresh(owed)
    assert owed.last_reminded_on == plant_today()

    done = (await db_session.execute(select(TbtDelivery).where(
        TbtDelivery.supervisor_id == month_of_talks["anna"].id))).scalars().first()
    refused = await client.post(f"/api/v1/toolbox-talks/deliveries/{done.id}/remind")
    assert refused.status_code == 409
    assert await _chases(db_session, done.id) == 0


async def test_remind_all_chases_only_that_months_outstanding(hse_manager, month_of_talks,
                                                             db_session):
    _, client = hse_manager
    sent = await client.post(f"/api/v1/toolbox-talks/remind-all?month={_month().isoformat()}")
    assert sent.status_code == 200, sent.text
    assert sent.json()["reminded"] == 1
    assert await _chases(db_session, month_of_talks["owed"].id) == 1
    # Last month's outstanding one is not this month's to chase.
    last = (await db_session.execute(
        select(TbtDelivery).join(ToolboxTalk, ToolboxTalk.id == TbtDelivery.talk_id)
        .where(ToolboxTalk.period_month == _month(-1)))).scalars().first()
    assert await _chases(db_session, last.id) == 0
