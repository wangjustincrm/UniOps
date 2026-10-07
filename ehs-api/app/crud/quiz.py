"""Building a quiz, taking one, and scoring it.

`score()` is the whole point of this module being on the server. It reads the
answer key, which never leaves here, and it is the only place a mark is
decided. Everything else is bookkeeping around it.

Passing writes a training record the same way a course session does, and stores
its id on the attempt so the two can never disagree about whether it was
issued. Somebody who has already passed does not get a second record for
passing again.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.exc import IntegrityError

from app.crud import course as course_crud
from app.models.course import CourseDelivery, CourseEnrolment
from app.models.mirrors import User
from app.models.quiz import Quiz, QuizAttempt, QuizQuestion
from app.models.training import Course, TrainingRecord
from app.services import statutory

QUESTION_KINDS = ("single", "multi", "true_false")


def _new_qr_token() -> str:
    return secrets.token_urlsafe(24)


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


# ── Building ─────────────────────────────────────────────────────────────────

async def list_quizzes(
    db: AsyncSession, *, course_id: uuid.UUID | None = None, active_only: bool = True
) -> list[Quiz]:
    stmt = select(Quiz)
    if course_id:
        stmt = stmt.where(Quiz.course_id == course_id)
    if active_only:
        stmt = stmt.where(Quiz.is_active.is_(True))
    return list((await db.execute(stmt.order_by(Quiz.title))).scalars())


async def get_quiz(db: AsyncSession, quiz_id: uuid.UUID) -> Quiz:
    quiz = await db.get(Quiz, quiz_id)
    if quiz is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quiz not found")
    return quiz


def require_active(quiz: Quiz) -> None:
    """Refuse a retired quiz to the person taking it.

    ★ Retiring is how HSE replaces an out-of-date quiz, and the old code does not
    retire with it: it is on the wall, in a photo, in somebody's history. Without
    this a scan of it would still join today's session, serve the old questions
    and, on a pass, certify against them. The list hides retired quizzes; that
    is not the same as refusing them.
    """
    if not quiz.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This quiz has been retired — ask the person running the session "
            "for the current code.")


async def by_qr_token(db: AsyncSession, token: str) -> Quiz | None:
    return (await db.execute(
        select(Quiz).where(Quiz.qr_token == token))).scalar_one_or_none()


async def create_quiz(db: AsyncSession, payload: dict, *, creator_id: uuid.UUID) -> Quiz:
    if payload.get("course_id") and await db.get(Course, payload["course_id"]) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found")
    quiz = Quiz(
        id=uuid.uuid4(),
        course_id=payload.get("course_id"),
        title=payload["title"],
        intro=payload.get("intro"),
        pass_mark_percent=payload.get("pass_mark_percent", 80),
        max_attempts=payload.get("max_attempts"),
        qr_token=_new_qr_token(),
        created_by=creator_id,
    )
    db.add(quiz)
    await db.flush()
    return quiz


async def questions_for(db: AsyncSession, quiz_id: uuid.UUID) -> list[QuizQuestion]:
    return list((await db.execute(
        select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.seq))).scalars())


async def replace_questions(
    db: AsyncSession, quiz: Quiz, questions: list[dict]
) -> list[QuizQuestion]:
    """Rewrite the question list wholesale.

    Refused once anybody has taken it. A quiz whose questions changed under the
    people who already answered them has scores that mean two different things
    and no way to tell which is which.
    """
    # Counted as attempts, and said as attempts: three goes by one person is
    # not three people, and a message that says otherwise is one somebody will
    # later quote at a meeting.
    taken = (await db.execute(
        select(func.count()).select_from(QuizAttempt)
        .where(QuizAttempt.quiz_id == quiz.id))).scalar() or 0
    if taken:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This quiz has already been answered {taken} "
            f"time{'s' if taken != 1 else ''}. Changing the questions now would "
            "leave those scores meaning something different from everyone "
            "else's — make a new quiz instead.")

    for existing in await questions_for(db, quiz.id):
        await db.delete(existing)
    await db.flush()

    rows: list[QuizQuestion] = []
    for index, q in enumerate(questions, start=1):
        kind = q.get("kind", "single")
        if kind not in QUESTION_KINDS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Unknown question type '{kind}'")
        options = q.get("options") or []
        correct = [str(c) for c in (q.get("correct") or [])]
        keys = {str(o.get("key")) for o in options}

        if not correct:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Question {index} has no right answer marked.")
        unknown = [c for c in correct if c not in keys]
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Question {index} marks {', '.join(unknown)} correct, but no such "
                "option exists.")
        if kind in ("single", "true_false") and len(correct) != 1:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Question {index} is a single-answer question with "
                f"{len(correct)} right answers marked.")

        row = QuizQuestion(
            id=uuid.uuid4(), quiz_id=quiz.id, seq=index,
            prompt=q["prompt"], kind=kind, options=options, correct=correct,
            explanation=q.get("explanation"), points=q.get("points", 1),
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


# ── Taking ───────────────────────────────────────────────────────────────────

async def attempts_for(
    db: AsyncSession, quiz_id: uuid.UUID, *, user_id: uuid.UUID | None = None
) -> list[QuizAttempt]:
    stmt = select(QuizAttempt).where(QuizAttempt.quiz_id == quiz_id)
    if user_id:
        stmt = stmt.where(QuizAttempt.user_id == user_id)
    return list((await db.execute(
        stmt.order_by(QuizAttempt.submitted_at.desc()))).scalars())


async def score(
    db: AsyncSession, quiz: Quiz, answers: dict[str, list[str]], *,
    user_id: uuid.UUID, delivery_id: uuid.UUID | None = None,
) -> tuple[QuizAttempt, list[dict]]:
    """Mark an attempt, and say which questions were wrong.

    The only place a mark is decided, and the only place the answer key is
    read. Returns the attempt and a per-question breakdown for the learner —
    a wrong answer with no explanation teaches nothing.

    A multi-answer question is right only when the set matches exactly. Partial
    credit sounds kinder and is wrong here: "name the three things you must do
    before entering a confined space" is not two-thirds satisfied by naming two.
    """
    require_active(quiz)
    questions = await questions_for(db, quiz.id)
    if not questions:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "This quiz has no questions yet")

    # ★ Only a session this person is actually on the roster of. The id comes
    # from the browser; taken on trust it would let anyone attach their attempt
    # to any session and, with a pass, be marked as having attended it.
    enrolment = None
    if delivery_id is not None:
        enrolment = await _enrolment_for(db, quiz, user_id, delivery_id)
        if enrolment is None:
            delivery_id = None

    mine = await attempts_for(db, quiz.id, user_id=user_id)
    if quiz.max_attempts is not None and len(mine) >= quiz.max_attempts:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You have used all {quiz.max_attempts} attempts at this quiz.")

    breakdown: list[dict] = []
    scored = 0
    possible = 0
    for q in questions:
        possible += q.points
        given = {str(a) for a in (answers.get(str(q.id)) or [])}
        expected = {str(c) for c in (q.correct or [])}
        right = given == expected
        if right:
            scored += q.points
        breakdown.append({
            "question_id": str(q.id),
            "prompt": q.prompt,
            "correct": right,
            "your_answer": sorted(given),
            "right_answer": sorted(expected),
            "explanation": q.explanation,
        })

    percent = Decimal(scored * 100) / Decimal(possible) if possible else Decimal(0)
    passed = float(percent) >= quiz.pass_mark_percent

    attempt = QuizAttempt(
        id=uuid.uuid4(), quiz_id=quiz.id, user_id=user_id,
        user_name=await _name(db, user_id) or "Unknown",
        delivery_id=delivery_id,
        submitted_at=datetime.now(timezone.utc),
        answers={k: list(v) for k, v in answers.items()},
        score_percent=percent.quantize(Decimal("0.01")),
        points_scored=scored, points_possible=possible,
        passed=passed,
    )
    db.add(attempt)
    await db.flush()

    if enrolment is not None:
        await _onto_roster(db, attempt, enrolment)
    elif passed:
        record = await _issue_record(db, quiz, attempt)
        if record is not None:
            attempt.training_record_id = record.id
            await db.flush()

    return attempt, breakdown


# Who the roster says marked a result the quiz decided. A person marking by hand
# always sets `marked_by`; this name with no `marked_by` is how a row the quiz
# wrote is told apart from one somebody decided.
QUIZ_MARKER = "Online quiz"


def _quiz_may_mark(enrolment: CourseEnrolment) -> bool:
    """Whether the quiz may set this person's result.

    ★ Never over a decision a person made. Round 4 gave the person running the
    session the say on pass and fail; the quiz fills in what nobody has decided
    yet — still enrolled, marked absent by closing the session, or a result the
    quiz itself wrote on an earlier attempt.
    """
    return enrolment.marked_by is None and enrolment.marked_by_name in (None, QUIZ_MARKER)


async def _enrolment_for(
    db: AsyncSession, quiz: Quiz, user_id: uuid.UUID, delivery_id: uuid.UUID,
) -> CourseEnrolment | None:
    if quiz.course_id is None:
        return None
    return (await db.execute(
        select(CourseEnrolment)
        .join(CourseDelivery, CourseDelivery.id == CourseEnrolment.delivery_id)
        .where(CourseEnrolment.delivery_id == delivery_id,
               CourseEnrolment.user_id == user_id,
               CourseDelivery.course_id == quiz.course_id,
               CourseDelivery.status != "cancelled"))).scalar_one_or_none()


async def _onto_roster(
    db: AsyncSession, attempt: QuizAttempt, enrolment: CourseEnrolment,
) -> None:
    """An attempt taken at a session, written back to that session's roster.

    Without this a person who scanned in and passed sits on the roster as
    `enrolled`, and closing the session turns them into `absent`.

    ★ One training record per person per session, whichever comes first. If the
    person running it already marked them passed, the attempt points at that
    record rather than issuing a second; if the quiz issues it, the enrolment
    keeps it, so marking them passed by hand afterwards does not issue another.
    """
    if attempt.passed:
        # A record is issued only where the quiz has the say. Somebody the
        # person running it marked failed is not certified by passing anyway.
        if enrolment.training_record_id is None and _quiz_may_mark(enrolment):
            delivery = await db.get(CourseDelivery, enrolment.delivery_id)
            record = await course_crud._issue_record(
                db, delivery, enrolment,
                recorded_by=attempt.user_id, recorded_by_name=attempt.user_name)
            enrolment.training_record_id = record.id
        attempt.training_record_id = enrolment.training_record_id

    # ★ Only upward. A later pass lifts an earlier quiz fail; once passed, a
    # retake that fails changes nothing — the result stays and so does the
    # record, which is somebody's statutory evidence.
    if _quiz_may_mark(enrolment) and enrolment.result != "passed":
        enrolment.result = "passed" if attempt.passed else "failed"
        enrolment.marked_by_name = QUIZ_MARKER
        enrolment.marked_at = attempt.submitted_at
    await db.flush()


# ★ How far either side of a session's times a scan still joins it. HSE's
# premise is that the code is only shown in the room, but it is a fixed URL: a
# photo of it in a group chat, or a browser's history, would otherwise put
# somebody who was not there on a statutory attendance record for the whole day.
JOIN_OPENS_BEFORE = timedelta(minutes=30)
JOIN_CLOSES_AFTER = timedelta(hours=2)


def _join_window(d: CourseDelivery) -> tuple[datetime, datetime] | None:
    """When a scan joins this session, or None for "any time that day".

    A session with no start time is judged by its date alone; one with a start
    and no end stays open until the day is over.
    """
    if d.start_time is None:
        return None
    start = datetime.combine(d.session_date, d.start_time, tzinfo=statutory.ONTARIO)
    if d.end_time is not None:
        end = datetime.combine(d.session_date, d.end_time, tzinfo=statutory.ONTARIO)
        closes = end + JOIN_CLOSES_AFTER
    else:
        closes = datetime.combine(d.session_date + timedelta(days=1), datetime.min.time(),
                                  tzinfo=statutory.ONTARIO)
    return start - JOIN_OPENS_BEFORE, closes


async def sessions_today(
    db: AsyncSession, quiz: Quiz, *, now: datetime | None = None,
) -> list[CourseDelivery]:
    """This course's sessions under way now, Ontario time, roster still open."""
    if quiz.course_id is None:
        return []
    now = (now or datetime.now(timezone.utc)).astimezone(statutory.ONTARIO)
    rows = list((await db.execute(
        select(CourseDelivery)
        .where(CourseDelivery.course_id == quiz.course_id,
               CourseDelivery.session_date == now.date(),
               CourseDelivery.status == "scheduled")
        .order_by(CourseDelivery.start_time.nulls_last(), CourseDelivery.delivery_no)
    )).scalars())
    out = []
    for d in rows:
        window = _join_window(d)
        if window is None or window[0] <= now < window[1]:
            out.append(d)
    return out


class JoinResult:
    def __init__(self, *, joined: CourseDelivery | None = None, added: bool = False,
                 choices: list[CourseDelivery] | None = None, note: str | None = None):
        self.joined, self.added = joined, added
        self.choices, self.note = choices or [], note


async def join(
    db: AsyncSession, quiz: Quiz, user_id: uuid.UUID, *,
    delivery_id: uuid.UUID | None = None, now: datetime | None = None,
) -> JoinResult:
    """Scanning the quiz code puts you on the roster of the session it was shown at.

    Round 5, answer box 4: "scanning the quiz code should allow for staff to be
    added to the roster as the only way a code is displayed is at the end of the
    course." So the session is the one of this course running today. One such
    session is the usual case and needs no question; two on one day (a morning
    and an afternoon sitting) needs the person to say which; none means there is
    nothing to join, and the quiz still counts on its own.

    Joining is at scan time rather than on passing, because what HSE is short
    of is attendance — somebody who sat the session and failed the quiz was
    still there.
    """
    require_active(quiz)
    if quiz.course_id is None:
        return JoinResult(note="This quiz is not part of a course, so there is no "
                               "session roster to join.")
    candidates = await sessions_today(db, quiz, now=now)
    if not candidates:
        return JoinResult(note="No session of this course is under way right now, so you "
                               "have not been added to a roster. Your result still counts; "
                               "ask the person running the course if you attended one.")

    if delivery_id is not None:
        chosen = next((d for d in candidates if d.id == delivery_id), None)
        if chosen is None:
            return JoinResult(choices=candidates,
                              note="That session is not under way right now. "
                                   "Pick the one you are in.")
    else:
        on = {e.delivery_id for e in (await db.execute(
            select(CourseEnrolment).where(
                CourseEnrolment.user_id == user_id,
                CourseEnrolment.delivery_id.in_([d.id for d in candidates])))).scalars()}
        mine = [d for d in candidates if d.id in on]
        if len(mine) == 1:
            return JoinResult(joined=mine[0])
        if len(candidates) > 1:
            return JoinResult(choices=candidates,
                              note="This course has more than one session today. "
                                   "Pick the one you are in.")
        chosen = candidates[0]

    already = (await db.execute(select(CourseEnrolment.id).where(
        CourseEnrolment.delivery_id == chosen.id,
        CourseEnrolment.user_id == user_id))).scalar()
    if already is not None:
        return JoinResult(joined=chosen)
    # ★ Savepoint: a phone that sends the scan twice must not fail the second on
    # the unique constraint and take the whole request down with it.
    try:
        async with db.begin_nested():
            await course_crud.enrol(db, chosen, [user_id], via="quiz")
    except IntegrityError:
        return JoinResult(joined=chosen)
    return JoinResult(joined=chosen, added=True)


async def _issue_record(
    db: AsyncSession, quiz: Quiz, attempt: QuizAttempt
) -> TrainingRecord | None:
    """The register entry a pass produces.

    Nothing is written when the quiz is not attached to a course — a standalone
    quiz is a teaching aid, and inventing a training record for it would put
    something in a person's file that no course backs.

    Passing again does not issue a second record.
    """
    if quiz.course_id is None or attempt.user_id is None:
        return None

    already = (await db.execute(
        select(QuizAttempt).where(
            QuizAttempt.quiz_id == quiz.id,
            QuizAttempt.user_id == attempt.user_id,
            QuizAttempt.passed.is_(True),
            QuizAttempt.training_record_id.is_not(None),
        ))).scalars().first()
    if already is not None:
        return None

    course = await db.get(Course, quiz.course_id)
    if course is None:
        return None

    completed = attempt.submitted_at.date()
    expires = (completed + timedelta(days=30 * course.validity_months)
               if course.validity_months else None)

    record = TrainingRecord(
        id=uuid.uuid4(),
        user_id=attempt.user_id,
        user_name=attempt.user_name,
        course_id=course.id,
        course_label=course.name,
        completed_on=completed,
        expires_on=expires,
        delivery="internal",
        recorded_by=attempt.user_id,
        recorded_by_name=attempt.user_name,
    )
    db.add(record)
    await db.flush()
    return record


async def results(db: AsyncSession, quiz_id: uuid.UUID) -> dict:
    """How the room did, and which question they got wrong.

    The second half is the useful output. A quiz where everybody missed
    question four has found a gap in the training, not a gap in four people.
    """
    attempts = await attempts_for(db, quiz_id)
    questions = await questions_for(db, quiz_id)

    # Latest attempt per person — retries are learning, and counting every one
    # would make a quiz look worse the more people improved on it.
    latest: dict[uuid.UUID, QuizAttempt] = {}
    for a in sorted(attempts, key=lambda x: x.submitted_at):
        if a.user_id:
            latest[a.user_id] = a

    taken = list(latest.values())
    passed = [a for a in taken if a.passed]

    wrong_counts: dict[str, int] = {}
    for a in taken:
        for q in questions:
            given = {str(x) for x in (a.answers.get(str(q.id)) or [])}
            if given != {str(c) for c in (q.correct or [])}:
                wrong_counts[str(q.id)] = wrong_counts.get(str(q.id), 0) + 1

    hardest = [{
        "question_id": str(q.id),
        "prompt": q.prompt,
        "got_it_wrong": wrong_counts.get(str(q.id), 0),
        "of": len(taken),
    } for q in questions]
    hardest.sort(key=lambda r: -r["got_it_wrong"])

    return {
        "people": len(taken),
        "passed": len(passed),
        "pass_rate": round(len(passed) * 100 / len(taken), 1) if taken else None,
        "average_score": (round(sum(float(a.score_percent) for a in taken) / len(taken), 1)
                          if taken else None),
        "total_attempts": len(attempts),
        "by_question": hardest,
    }
