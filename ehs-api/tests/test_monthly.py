"""What a supervisor owes each month: a toolbox talk, a hazard assessment and a
near miss.

Round 5, answer box 11: "It is one of each per supervisor per calendar month,
reset on the 1st. an unmet item in a month is simply missed. a hazard assessment
is a hazard register." Derived from the records, so these tests plant records and
read the answer — there is no obligation row to inspect.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.hazard import Hazard
from app.models.incident import Incident
from app.models.toolbox import TbtDelivery, ToolboxTalk
from app.services import monthly
from tests.conftest import make_user

OCT = date(2026, 10, 15)


def _by_key(month) -> dict:
    return {i.key: i for i in month.items}


async def _hazard(db, *, by, at: datetime):
    h = Hazard(id=uuid.uuid4(), hazard_no=f"HAZ-T-{uuid.uuid4().hex[:6]}",
               title="A test hazard", created_by=by, created_at=at)
    db.add(h)
    await db.flush()
    return h


async def _near_miss(db, *, by, submitted: datetime | None):
    i = Incident(id=uuid.uuid4(), incident_no=f"INC-T-{uuid.uuid4().hex[:6]}",
                 form_kind="near_miss", title="A near miss", reported_by=by,
                 status="submitted" if submitted else "draft",
                 occurred_at=(submitted or datetime.now(timezone.utc)) - timedelta(hours=1),
                 submitted_at=submitted)
    db.add(i)
    await db.flush()
    return i


class TestWhoIsAsked:
    async def test_somebody_who_runs_no_crew_is_asked_for_nothing(
            self, test_engine, db_session):
        """Empty rather than refused: My Actions is a page everyone opens."""
        worker = await make_user(test_engine, role="requester")
        assert await monthly.for_user(db_session, worker.id, "requester", today=OCT) == []

    async def test_a_supervisor_by_primary_role_is_asked(self, test_engine, db_session):
        sup = await make_user(test_engine, role="area_supervisor")
        months = await monthly.for_user(db_session, sup.id, "area_supervisor", today=OCT)
        assert [m.month for m in months] == ["2026-10", "2026-11"]
        assert months[0].is_current and not months[1].is_current
        assert [i.key for i in months[0].items] == [
            "toolbox_talk", "hazard_assessment", "near_miss"]

    async def test_a_supervisor_by_additional_role_is_asked_too(
            self, test_engine, db_session):
        """★ Area supervisor is designed to be held alongside a job, not as the
        primary role. Reading only the primary role is the mistake that has
        already cost this repository once."""
        person = await make_user(test_engine, role="requester")
        await db_session.execute(
            text("INSERT INTO user_roles (user_id, role_code) "
                 "VALUES (:u, 'area_supervisor')"), {"u": person.id})
        await db_session.flush()
        months = await monthly.for_user(db_session, person.id, "requester", today=OCT)
        assert len(months) == 2


class TestTheHazardAssessment:
    async def test_one_recorded_this_month_counts(self, test_engine, db_session):
        sup = await make_user(test_engine, role="area_supervisor")
        await _hazard(db_session, by=sup.id,
                      at=datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc))
        this = _by_key((await monthly.for_user(
            db_session, sup.id, "area_supervisor", today=OCT))[0])
        assert this["hazard_assessment"].done
        assert this["hazard_assessment"].count == 1

    async def test_last_month_does_not_count_for_this_one(self, test_engine, db_session):
        """Reset on the 1st."""
        sup = await make_user(test_engine, role="area_supervisor")
        await _hazard(db_session, by=sup.id,
                      at=datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc))
        this = _by_key((await monthly.for_user(
            db_session, sup.id, "area_supervisor", today=OCT))[0])
        assert not this["hazard_assessment"].done

    async def test_the_month_ends_at_midnight_in_ontario_not_in_utc(
            self, test_engine, db_session):
        """★ 00:30 UTC on 1 October is 20:30 on 30 September in Kingston. A hazard
        recorded on that evening belongs to September — the dashboard once drew
        its year boundary at midnight UTC and moved a whole evening's reports."""
        sup = await make_user(test_engine, role="area_supervisor")
        await _hazard(db_session, by=sup.id,
                      at=datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc))

        october = _by_key((await monthly.for_user(
            db_session, sup.id, "area_supervisor", today=OCT))[0])
        assert not october["hazard_assessment"].done, "it is a September hazard"

        september = _by_key((await monthly.for_user(
            db_session, sup.id, "area_supervisor", today=date(2026, 9, 15)))[0])
        assert september["hazard_assessment"].done

    async def test_a_hazard_somebody_else_recorded_and_handed_over_does_not_count(
            self, test_engine, db_session):
        """★ Recorded, not owned. On the dev database two hazards were recorded by
        HSE and handed to a supervisor to own; crediting the owner would give that
        supervisor an assessment they never made."""
        sup = await make_user(test_engine, role="area_supervisor")
        hse = await make_user(test_engine, role="ehs_manager")
        h = await _hazard(db_session, by=hse.id,
                          at=datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc))
        h.owner_id = sup.id
        await db_session.flush()
        this = _by_key((await monthly.for_user(
            db_session, sup.id, "area_supervisor", today=OCT))[0])
        assert not this["hazard_assessment"].done


class TestTheNearMiss:
    async def test_a_submitted_one_counts(self, test_engine, db_session):
        sup = await make_user(test_engine, role="area_supervisor")
        await _near_miss(db_session, by=sup.id,
                         submitted=datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc))
        this = _by_key((await monthly.for_user(
            db_session, sup.id, "area_supervisor", today=OCT))[0])
        assert this["near_miss"].done

    async def test_a_draft_does_not(self, test_engine, db_session):
        """A draft has not been reported to anybody."""
        sup = await make_user(test_engine, role="area_supervisor")
        await _near_miss(db_session, by=sup.id, submitted=None)
        this = _by_key((await monthly.for_user(
            db_session, sup.id, "area_supervisor", today=OCT))[0])
        assert not this["near_miss"].done


class TestTheToolboxTalk:
    async def test_no_talk_published_says_so(self, test_engine, db_session):
        """★ Not the supervisor's miss. HSE publishes the topic; until then there
        is nothing to hold, and the screen must not read as if they failed."""
        sup = await make_user(test_engine, role="area_supervisor")
        this = _by_key((await monthly.for_user(
            db_session, sup.id, "area_supervisor", today=OCT))[0])
        assert not this["toolbox_talk"].done
        assert "No talk has been published" in this["toolbox_talk"].detail


    async def _talk(self, db, sup, *, title, created, status):
        t = ToolboxTalk(id=uuid.uuid4(), talk_no=f"TBT-T-{uuid.uuid4().hex[:6]}",
                        title=title, period_month=date(2026, 10, 1), is_published=True,
                        created_at=created)
        db.add(t)
        await db.flush()
        db.add(TbtDelivery(id=uuid.uuid4(), talk_id=t.id, supervisor_id=sup.id,
                           status=status))
        await db.flush()

    async def test_two_talks_in_one_month_one_held_is_done(self, test_engine, db_session):
        """★ period_month is not unique and both land on the 1st. Box 11 asks for
        one a month, so holding either meets it — whichever row the database
        happens to return first must not decide that."""
        sup = await make_user(test_engine, role="area_supervisor")
        base = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
        await self._talk(db_session, sup, title="Ladders", created=base, status="pending")
        await self._talk(db_session, sup, title="Ladders (corrected)",
                         created=base + timedelta(days=1), status="delivered")
        tbt = _by_key((await monthly.for_user(
            db_session, sup.id, "area_supervisor", today=OCT))[0])["toolbox_talk"]
        assert tbt.done
        assert "corrected" in tbt.detail

    async def test_two_open_talks_point_at_the_earliest(self, test_engine, db_session):
        sup = await make_user(test_engine, role="area_supervisor")
        base = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
        await self._talk(db_session, sup, title="Second", created=base + timedelta(days=1),
                         status="pending")
        await self._talk(db_session, sup, title="First", created=base, status="pending")
        tbt = _by_key((await monthly.for_user(
            db_session, sup.id, "area_supervisor", today=OCT))[0])["toolbox_talk"]
        assert not tbt.done
        assert "“First”" in tbt.detail


async def test_the_endpoint_answers_for_the_caller(hse_manager, worker):
    """Empty for somebody who runs no crew, from the page everyone opens."""
    _, client = worker
    got = await client.get("/api/v1/actions/my-month")
    assert got.status_code == 200, got.text
    assert got.json() == []
