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

from app.models.mirrors import User
from app.models.quiz import Quiz, QuizAttempt, QuizQuestion
from app.models.training import Course, TrainingRecord

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
    questions = await questions_for(db, quiz.id)
    if not questions:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "This quiz has no questions yet")

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

    if passed:
        record = await _issue_record(db, quiz, attempt)
        if record is not None:
            attempt.training_record_id = record.id
            await db.flush()

    return attempt, breakdown


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
