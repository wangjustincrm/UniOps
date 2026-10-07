"""Scanning the quiz code puts you on the roster of the session it was shown at.

Round 5, answer box 4: "I would like everyone the ability to scan the code and
get the test and be added to the roster. attendance at training can be hard to
track ... the only way a code is displayed is at the end of the course."

Most of these call the crud with a fixed `now`, because which session a scan
joins depends on the clock. One goes through the API end to end.
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.crud import course as course_crud
from app.crud import quiz as quiz_crud
from app.models.course import CourseDelivery, CourseEnrolment
from app.models.quiz import Quiz, QuizQuestion
from app.models.training import Course, TrainingRecord
from app.services import statutory
from tests.conftest import make_user

DAY = date(2026, 10, 14)


def at(hh: int, mm: int = 0, day: date = DAY) -> datetime:
    """A moment on the plant's clock."""
    return datetime.combine(day, time(hh, mm), tzinfo=statutory.ONTARIO)


async def _course(db) -> Course:
    c = Course(id=uuid.uuid4(), code=f"T-{uuid.uuid4().hex[:6]}", name="Lockout / tagout",
               validity_months=12)
    db.add(c)
    await db.flush()
    return c


async def _quiz(db, course: Course | None) -> Quiz:
    q = Quiz(id=uuid.uuid4(), course_id=course.id if course else None,
             title="LOTO check", pass_mark_percent=100)
    db.add(q)
    await db.flush()
    db.add(QuizQuestion(id=uuid.uuid4(), quiz_id=q.id, seq=1, prompt="Lock before work?",
                        kind="true_false",
                        options=[{"key": "t", "label": "True"}, {"key": "f", "label": "False"}],
                        correct=["t"]))
    await db.flush()
    return q


async def _session(db, course: Course, *, day: date = DAY, start: time | None = None,
                   end: time | None = None, status: str = "scheduled") -> CourseDelivery:
    d = CourseDelivery(id=uuid.uuid4(), delivery_no=f"CS-T-{uuid.uuid4().hex[:6]}",
                       course_id=course.id, course_label=course.name, title=course.name,
                       session_date=day, start_time=start, end_time=end, status=status)
    db.add(d)
    await db.flush()
    return d


async def _answer(db, quiz: Quiz, user, *, right: bool, delivery: CourseDelivery | None):
    question = (await quiz_crud.questions_for(db, quiz.id))[0]
    attempt, _ = await quiz_crud.score(
        db, quiz, {str(question.id): ["t" if right else "f"]},
        user_id=user.id, delivery_id=delivery.id if delivery else None)
    return attempt


async def _enrolment(db, delivery, user) -> CourseEnrolment | None:
    return (await db.execute(select(CourseEnrolment).where(
        CourseEnrolment.delivery_id == delivery.id,
        CourseEnrolment.user_id == user.id))).scalar_one_or_none()


async def _records(db, user) -> int:
    return (await db.execute(select(func.count()).select_from(TrainingRecord).where(
        TrainingRecord.user_id == user.id))).scalar()


class TestWhichSession:
    async def test_one_session_under_way_is_joined_without_a_question(
            self, test_engine, db_session):
        me = await make_user(test_engine)
        course = await _course(db_session)
        quiz = await _quiz(db_session, course)
        s = await _session(db_session, course)

        got = await quiz_crud.join(db_session, quiz, me.id, now=at(10))
        assert got.joined.id == s.id and got.added and not got.choices
        row = await _enrolment(db_session, s, me)
        assert row.joined_via == "quiz" and row.result == "enrolled"

        again = await quiz_crud.join(db_session, quiz, me.id, now=at(10, 5))
        assert again.joined.id == s.id and not again.added

    async def test_two_at_once_asks_which(self, test_engine, db_session):
        me = await make_user(test_engine)
        course = await _course(db_session)
        quiz = await _quiz(db_session, course)
        a = await _session(db_session, course)
        b = await _session(db_session, course)

        got = await quiz_crud.join(db_session, quiz, me.id, now=at(10))
        assert got.joined is None and {d.id for d in got.choices} == {a.id, b.id}
        assert await _enrolment(db_session, a, me) is None
        assert await _enrolment(db_session, b, me) is None

        picked = await quiz_crud.join(db_session, quiz, me.id, delivery_id=b.id, now=at(10))
        assert picked.joined.id == b.id and picked.added

    async def test_already_on_one_of_two_is_not_asked(self, test_engine, db_session):
        me = await make_user(test_engine)
        course = await _course(db_session)
        quiz = await _quiz(db_session, course)
        await _session(db_session, course)
        b = await _session(db_session, course)
        await course_crud.enrol(db_session, b, [me.id])

        got = await quiz_crud.join(db_session, quiz, me.id, now=at(10))
        assert got.joined.id == b.id and not got.choices and not got.added

    async def test_no_session_today_joins_nothing_and_says_so(self, test_engine, db_session):
        me = await make_user(test_engine)
        course = await _course(db_session)
        quiz = await _quiz(db_session, course)
        yesterday = await _session(db_session, course, day=DAY - timedelta(days=1))

        got = await quiz_crud.join(db_session, quiz, me.id, now=at(10))
        assert got.joined is None and not got.choices
        assert "not been added" in got.note
        assert await _enrolment(db_session, yesterday, me) is None

    async def test_a_closed_session_is_not_joined(self, test_engine, db_session):
        me = await make_user(test_engine)
        course = await _course(db_session)
        quiz = await _quiz(db_session, course)
        await _session(db_session, course, status="completed")
        await _session(db_session, course, status="cancelled")

        got = await quiz_crud.join(db_session, quiz, me.id, now=at(10))
        assert got.joined is None

    async def test_a_quiz_with_no_course_has_no_roster(self, test_engine, db_session):
        me = await make_user(test_engine)
        quiz = await _quiz(db_session, None)
        got = await quiz_crud.join(db_session, quiz, me.id, now=at(10))
        assert got.joined is None and "not part of a course" in got.note


class TestTheWindow:
    """★ The code is a fixed URL. A photo of it in a group chat must not put
    somebody on a statutory attendance record for the whole day."""

    async def test_from_half_an_hour_before_to_two_hours_after(self, test_engine, db_session):
        course = await _course(db_session)
        quiz = await _quiz(db_session, course)
        s = await _session(db_session, course, start=time(9), end=time(10))

        async def joins(now):
            me = await make_user(test_engine)
            got = await quiz_crud.join(db_session, quiz, me.id, now=now)
            return got.joined is not None and got.joined.id == s.id

        assert not await joins(at(8, 29))
        assert await joins(at(8, 30))
        assert await joins(at(11, 59))
        assert not await joins(at(12, 0))

    async def test_a_start_and_no_end_stays_open_for_the_day(self, test_engine, db_session):
        me = await make_user(test_engine)
        course = await _course(db_session)
        quiz = await _quiz(db_session, course)
        await _session(db_session, course, start=time(9))
        got = await quiz_crud.join(db_session, quiz, me.id, now=at(23, 30))
        assert got.joined is not None

    async def test_utc_midnight_is_still_the_plant_s_day(self, test_engine, db_session):
        """01:00 UTC on the 15th is 21:00 on the 14th in Kingston."""
        me = await make_user(test_engine)
        course = await _course(db_session)
        quiz = await _quiz(db_session, course)
        s = await _session(db_session, course)
        got = await quiz_crud.join(db_session, quiz, me.id,
                                   now=datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc))
        assert got.joined.id == s.id


class TestTheResult:
    async def _on_roster(self, test_engine, db):
        me = await make_user(test_engine)
        course = await _course(db)
        quiz = await _quiz(db, course)
        s = await _session(db, course)
        await quiz_crud.join(db, quiz, me.id, now=at(10))
        return me, quiz, s

    async def test_a_pass_marks_the_roster_and_issues_one_record(self, test_engine, db_session):
        me, quiz, s = await self._on_roster(test_engine, db_session)
        attempt = await _answer(db_session, quiz, me, right=True, delivery=s)

        row = await _enrolment(db_session, s, me)
        assert row.result == "passed" and row.marked_by is None
        assert row.marked_by_name == quiz_crud.QUIZ_MARKER
        assert attempt.delivery_id == s.id
        assert attempt.training_record_id == row.training_record_id is not None
        record = await db_session.get(TrainingRecord, row.training_record_id)
        assert record.delivery_id == s.id and record.completed_on == s.session_date
        assert await _records(db_session, me) == 1

    async def test_marking_them_passed_by_hand_afterwards_issues_no_second(
            self, test_engine, db_session):
        me, quiz, s = await self._on_roster(test_engine, db_session)
        await _answer(db_session, quiz, me, right=True, delivery=s)
        row = await _enrolment(db_session, s, me)
        coord = await make_user(test_engine, role="ehs_coordinator")
        await course_crud.mark_results(db_session, s, {row.id: "passed"}, marker_id=coord.id)
        assert await _records(db_session, me) == 1

    async def test_passed_by_hand_first_the_quiz_points_at_that_record(
            self, test_engine, db_session):
        me, quiz, s = await self._on_roster(test_engine, db_session)
        row = await _enrolment(db_session, s, me)
        coord = await make_user(test_engine, role="ehs_coordinator")
        await course_crud.mark_results(db_session, s, {row.id: "passed"}, marker_id=coord.id)

        attempt = await _answer(db_session, quiz, me, right=True, delivery=s)
        assert attempt.training_record_id == row.training_record_id
        assert await _records(db_session, me) == 1
        assert row.marked_by == coord.id

    async def test_failed_by_hand_is_not_overturned_by_the_quiz(self, test_engine, db_session):
        me, quiz, s = await self._on_roster(test_engine, db_session)
        row = await _enrolment(db_session, s, me)
        coord = await make_user(test_engine, role="ehs_coordinator")
        await course_crud.mark_results(db_session, s, {row.id: "failed"}, marker_id=coord.id)

        attempt = await _answer(db_session, quiz, me, right=True, delivery=s)
        assert row.result == "failed" and row.marked_by == coord.id
        assert attempt.training_record_id is None
        assert await _records(db_session, me) == 0

    async def test_pass_then_fail_stays_passed(self, test_engine, db_session):
        """★ Only upward. The record is somebody's statutory evidence."""
        me, quiz, s = await self._on_roster(test_engine, db_session)
        await _answer(db_session, quiz, me, right=True, delivery=s)
        row = await _enrolment(db_session, s, me)
        record_id = row.training_record_id

        await _answer(db_session, quiz, me, right=False, delivery=s)
        assert row.result == "passed" and row.training_record_id == record_id
        assert await db_session.get(TrainingRecord, record_id) is not None

    async def test_fail_then_pass_is_passed(self, test_engine, db_session):
        me, quiz, s = await self._on_roster(test_engine, db_session)
        await _answer(db_session, quiz, me, right=False, delivery=s)
        row = await _enrolment(db_session, s, me)
        assert row.result == "failed" and row.training_record_id is None

        await _answer(db_session, quiz, me, right=True, delivery=s)
        assert row.result == "passed" and row.training_record_id is not None

    async def test_closing_the_session_then_passing_lifts_absent(self, test_engine, db_session):
        """Closing marks the unmarked absent; nobody decided that by hand."""
        me, quiz, s = await self._on_roster(test_engine, db_session)
        await course_crud.complete(db_session, s)
        row = await _enrolment(db_session, s, me)
        assert row.result == "absent"
        await _answer(db_session, quiz, me, right=True, delivery=s)
        assert row.result == "passed"

    async def test_a_session_you_are_not_on_is_ignored(self, test_engine, db_session):
        """★ The id comes from the browser. Taken on trust it would mark
        anybody as having attended any session."""
        me = await make_user(test_engine)
        course = await _course(db_session)
        quiz = await _quiz(db_session, course)
        s = await _session(db_session, course)

        attempt = await _answer(db_session, quiz, me, right=True, delivery=s)
        assert attempt.delivery_id is None
        assert await _enrolment(db_session, s, me) is None


class TestARetiredQuiz:
    """★ The old code outlives the quiz: on the wall, in a photo, in history."""

    async def test_does_not_join_or_mark(self, test_engine, db_session):
        me = await make_user(test_engine)
        course = await _course(db_session)
        quiz = await _quiz(db_session, course)
        s = await _session(db_session, course)
        quiz.is_active = False
        await db_session.flush()

        with pytest.raises(HTTPException) as joined:
            await quiz_crud.join(db_session, quiz, me.id, now=at(10))
        assert joined.value.status_code == 409
        assert await _enrolment(db_session, s, me) is None

        with pytest.raises(HTTPException) as taken:
            await _answer(db_session, quiz, me, right=True, delivery=None)
        assert taken.value.status_code == 409
        assert await _records(db_session, me) == 0

    async def test_the_questions_are_refused_too(self, worker, db_session):
        _, client = worker
        quiz = await _quiz(db_session, await _course(db_session))
        assert (await client.get(f"/api/v1/quizzes/{quiz.id}/questions")).status_code == 200
        quiz.is_active = False
        await db_session.flush()
        got = await client.get(f"/api/v1/quizzes/{quiz.id}/questions")
        assert got.status_code == 409 and "retired" in got.text


async def test_through_the_api(hse_manager, worker, db_session):
    """Scan, join, pass, and the roster says how they got there."""
    me, client = worker
    _, hse = hse_manager
    course = await _course(db_session)
    quiz = await _quiz(db_session, course)
    # No times: judged by the date alone, so this does not depend on the hour
    # the suite runs.
    today = datetime.now(statutory.ONTARIO).date()
    s = await _session(db_session, course, day=today)

    joined = await client.post(f"/api/v1/quizzes/{quiz.id}/join", json={})
    assert joined.status_code == 200, joined.text
    body = joined.json()
    assert body["joined"]["id"] == str(s.id) and body["added"] is True

    question = (await quiz_crud.questions_for(db_session, quiz.id))[0]
    taken = await client.post(f"/api/v1/quizzes/{quiz.id}/attempts", json={
        "answers": {str(question.id): ["t"]}, "delivery_id": str(s.id)})
    assert taken.status_code == 201, taken.text
    assert taken.json()["passed"] and taken.json()["training_record_id"]

    roster = await hse.get(f"/api/v1/course-sessions/{s.id}/roster")
    assert roster.status_code == 200, roster.text
    [row] = [r for r in roster.json() if r["user_id"] == str(me.id)]
    assert row["joined_via"] == "quiz" and row["result"] == "passed"
    assert row["marked_by_name"] == "Online quiz"
