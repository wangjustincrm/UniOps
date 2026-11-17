"""Building a quiz, taking one, and scoring it.

`score()` is the whole point of this module being on the server. It reads the
answer key, which never leaves here, and it is the only place a mark is
decided. Everything else is bookkeeping around it.

Passing does not write a training record. Round 6: a pass taken at a session
marks "Quiz passed" on that roster, and the certificate is issued when the
person running the session marks them passed and closes it.
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
from app.models.training import Course
from app.models.quiz import Quiz, QuizAttempt, QuizQuestion
from app.services import statutory

QUESTION_KINDS = ("single", "multi", "true_false", "rating", "likert", "comment")

# ★ UAT 15: "create a way to add a training course feedback form on some
# training courses … add these questions to the quiz": a 1–4 star rating, an
# Excellent / Good / Average / Poor / N/A choice "with no right answer", and a
# comment box. They ask for an opinion, so they are never marked: no answer
# key, no points, nothing towards the pass mark, and they may be skipped.
FEEDBACK_KINDS = ("rating", "likert", "comment")
# The choices are the server's, the same on every quiz, so the results add up.
FIXED_OPTIONS = {
    "rating": [{"key": "1", "label": "Below Average"}, {"key": "2", "label": "Average"},
               {"key": "3", "label": "Good"}, {"key": "4", "label": "Excellent"}],
    "likert": [{"key": "excellent", "label": "Excellent"}, {"key": "good", "label": "Good"},
               {"key": "average", "label": "Average"}, {"key": "poor", "label": "Poor"},
               {"key": "na", "label": "N/A"}],
    "comment": [],
}
COMMENT_MAX = 2000


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


def has_questions():
    """A quiz with no questions tests nothing, and nobody can pass it.

    ★ Round-6 review: a quiz created on a document and not yet written held
    signing shut for everybody — there was nothing to pass.
    """
    return select(QuizQuestion.id).where(QuizQuestion.quiz_id == Quiz.id).exists()


async def for_document(db: AsyncSession, document_id: uuid.UUID) -> Quiz | None:
    """The quiz a document asks for before it is signed, if it has one."""
    return (await db.execute(
        select(Quiz).where(Quiz.document_id == document_id, Quiz.is_active.is_(True),
                           has_questions())
        .order_by(Quiz.created_at.desc()).limit(1))).scalars().first()


async def retire(db: AsyncSession, quiz: Quiz) -> Quiz:
    """Stop asking for this quiz. Its attempts are kept; nothing is deleted."""
    quiz.is_active = False
    await db.flush()
    return quiz


async def has_passed(db: AsyncSession, quiz_id: uuid.UUID, user_id: uuid.UUID,
                     *, since: datetime | None = None) -> bool:
    stmt = select(QuizAttempt.id).where(QuizAttempt.quiz_id == quiz_id,
                                        QuizAttempt.user_id == user_id,
                                        QuizAttempt.passed.is_(True))
    if since is not None:
        stmt = stmt.where(QuizAttempt.submitted_at >= since)
    return (await db.execute(stmt.limit(1))).first() is not None


async def _version_published_at(db: AsyncSession, quiz: Quiz) -> datetime | None:
    """When the version a document's quiz now tests was published; None for a
    course's quiz, whose attempts all count."""
    if quiz.document_id is None:
        return None
    from app.models.document import SafetyDocument
    doc = await db.get(SafetyDocument, quiz.document_id)
    return doc.published_at if doc else None


async def passed_for_document(db: AsyncSession, quiz_id: uuid.UUID, document,
                              user_id: uuid.UUID) -> bool:
    """A pass that counts for this version of the document.

    ★ Round 7, question 2, answered by HSE: "a revised document requires a quiz
    to be answered again. a previous pass on a quiz does not automatically pass
    a revised document." The quiz moves to the new version on publishing, so
    only an attempt made since this version was published counts.
    """
    return await has_passed(db, quiz_id, user_id, since=document.published_at)


async def by_qr_token(db: AsyncSession, token: str) -> Quiz | None:
    return (await db.execute(
        select(Quiz).where(Quiz.qr_token == token))).scalar_one_or_none()


async def create_quiz(db: AsyncSession, payload: dict, *, creator_id: uuid.UUID) -> Quiz:
    if payload.get("course_id") and await db.get(Course, payload["course_id"]) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found")
    if payload.get("course_id") and payload.get("document_id"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A quiz tests a course or a document, not both")
    if payload.get("document_id"):
        from app.models.document import SafetyDocument
        if await db.get(SafetyDocument, payload["document_id"]) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    quiz = Quiz(
        id=uuid.uuid4(),
        course_id=payload.get("course_id"),
        document_id=payload.get("document_id"),
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
            "else's — copy it (Quizzes → Copy) and change the copy instead.")

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
        if kind in FEEDBACK_KINDS:
            row = QuizQuestion(
                id=uuid.uuid4(), quiz_id=quiz.id, seq=index, prompt=q["prompt"], kind=kind,
                options=FIXED_OPTIONS[kind], correct=[], explanation=None, points=0)
            db.add(row)
            rows.append(row)
            continue
        options = q.get("options") or []
        correct = [str(c) for c in (q.get("correct") or [])]
        keys = {str(o.get("key")) for o in options}

        if len(options) < 2:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Question {index} needs at least two options.")
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
    # ★ A document's quiz is retaken for each version (passed_for_document), so
    # its attempts are counted for this version too. Counted across versions,
    # somebody who used their attempts passing v1 could never pass — and never
    # sign — v2. Raised in review by uniops-55.
    since = await _version_published_at(db, quiz)
    if since is not None:
        mine = [a for a in mine if a.submitted_at >= since]
    if quiz.max_attempts is not None and len(mine) >= quiz.max_attempts:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You have used all {quiz.max_attempts} attempts at this quiz.")

    breakdown: list[dict] = []
    scored = 0
    possible = 0
    for q in questions:
        if q.kind in FEEDBACK_KINDS:
            _check_feedback(q, answers.get(str(q.id)) or [])
            continue
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

    # ★ Round 6: passing the quiz does not pass the course. It marks the roster;
    # the person running the session marks the pass, and the certificate is
    # issued when they close it. A pass away from any session marks nothing —
    # the result is kept here, and whoever runs a session can mark it there.
    # ★ Only a pass ends the session. Any answer did: a blank sheet handed in
    # two minutes in set the end, and the join window closed thirty minutes
    # later on everyone still to come — their attempts then hung off no
    # session, could not be marked, and were recorded absent, under a session
    # that had "run" for two minutes. Raised in review by uniops-93.
    if enrolment is not None and passed:
        await _end_from_first_finish(db, enrolment.delivery_id, attempt.submitted_at)
        await _quiz_mark(db, enrolment)

    return attempt, breakdown


def _check_feedback(q: QuizQuestion, given: list[str]) -> None:
    """A feedback answer is optional, but one given must be one of its
    choices — or, for a comment, one piece of text of a sensible length."""
    if not given:
        return
    if q.kind == "comment":
        if len(given) != 1 or len(str(given[0])) > COMMENT_MAX:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"A comment is one piece of text of at most {COMMENT_MAX} characters.")
        return
    keys = {o["key"] for o in FIXED_OPTIONS[q.kind]}
    if len(given) != 1 or str(given[0]) not in keys:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"“{q.prompt}” takes one of its choices.")


QUIZ_MARKER = "Online quiz"


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
               # ★ Review (uniops-8b): only a session still open. A closed
               # roster is the record its certificates came from: a pass sent
               # in afterwards by somebody recorded absent stamped it anyway.
               CourseDelivery.status == "scheduled"))).scalar_one_or_none()


async def _end_from_first_finish(
    db: AsyncSession, delivery_id: uuid.UUID, at: datetime,
) -> None:
    """The first finished quiz is when the session ended, if nobody said.

    Round 6: "Could the first person to log in and scan the code and answer the
    quiz (date and time stamped) be the end time of the course for the rest of
    the staff roster? this course length also needs to be on any reports."

    Only on the session's own day and after its start, so a late retake or a
    clock that disagrees cannot record a session that ended before it began. An
    end the runner typed is never overwritten.
    """
    delivery = await db.get(CourseDelivery, delivery_id)
    if delivery is None or delivery.end_time is not None:
        return
    local = at.astimezone(statutory.ONTARIO)
    if local.date() != delivery.session_date:
        return
    end = local.time().replace(second=0, microsecond=0)
    if delivery.start_time is not None and end <= delivery.start_time:
        return
    delivery.end_time = end
    delivery.end_time_from_quiz = True
    await db.flush()


async def _quiz_mark(db: AsyncSession, enrolment: CourseEnrolment) -> None:
    """Set "Quiz passed" on the roster. Only ever set, never taken away by a
    later failed retake — the pass happened."""
    if enrolment.quiz_passed_at is None:
        enrolment.quiz_passed_at = datetime.now(timezone.utc)
        enrolment.quiz_marked_by_name = QUIZ_MARKER
        await db.flush()


# ★ How far either side of a session's times a scan still joins it. HSE's
# premise is that the code is only shown in the room, but it is a fixed URL: a
# photo of it in a group chat, or a browser's history, would otherwise put
# somebody who was not there on a statutory attendance record for the whole day.
JOIN_OPENS_BEFORE = timedelta(minutes=30)
# Round 6: "allow the QR code to be scanned within 30 minutes after the course
# has completed." The end is the typed one, or the first finished quiz.
JOIN_CLOSES_AFTER = timedelta(minutes=30)


def _join_window(d: CourseDelivery) -> tuple[datetime, datetime] | None:
    """When a scan joins this session, or None for "any time that day".

    A session with no start time is judged by its date alone; one with a start
    and no end stays open until the day is over.

    ★ Only an end somebody entered closes the window. An end inferred from the
    first quiz pass is a record of how long it ran and nothing more: an early
    pass would otherwise shut out everyone still in the room. Raised in review
    by uniops-93.
    """
    if d.start_time is None:
        return None
    start = datetime.combine(d.session_date, d.start_time, tzinfo=statutory.ONTARIO)
    if d.end_time is not None and not d.end_time_from_quiz:
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
        # A document's quiz, or one on its own: there is no roster to speak of,
        # and saying so on a policy's quiz would only puzzle the person signing.
        return JoinResult(note=None if quiz.document_id else
                          "This quiz is not part of a course, so there is no "
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
    marked = [q for q in questions if q.kind not in FEEDBACK_KINDS]
    for a in taken:
        for q in marked:
            given = {str(x) for x in (a.answers.get(str(q.id)) or [])}
            if given != {str(c) for c in (q.correct or [])}:
                wrong_counts[str(q.id)] = wrong_counts.get(str(q.id), 0) + 1

    hardest = [{
        "question_id": str(q.id),
        "prompt": q.prompt,
        "got_it_wrong": wrong_counts.get(str(q.id), 0),
        "of": len(taken),
    } for q in marked]
    hardest.sort(key=lambda r: -r["got_it_wrong"])

    return {
        "people": len(taken),
        "passed": len(passed),
        "pass_rate": round(len(passed) * 100 / len(taken), 1) if taken else None,
        "average_score": (round(sum(float(a.score_percent) for a in taken) / len(taken), 1)
                          if taken else None),
        "total_attempts": len(attempts),
        "by_question": hardest,
        "feedback": _feedback(questions, taken),
    }


def _feedback(questions: list[QuizQuestion], taken: list[QuizAttempt]) -> list[dict]:
    """★ UAT 15: what people said, per feedback question — how many chose
    each answer, the average star rating, and the comments. No names: it is
    the course's feedback, not a mark against anybody."""
    out = []
    for q in questions:
        if q.kind not in FEEDBACK_KINDS:
            continue
        given = [(a.answers.get(str(q.id)) or []) for a in taken]
        given = [g[0] for g in given if g]
        row = {"question_id": str(q.id), "prompt": q.prompt, "kind": q.kind,
               "answered": len(given), "of": len(taken), "counts": [], "average": None,
               "comments": []}
        if q.kind == "comment":
            row["comments"] = [str(g).strip() for g in given if str(g).strip()]
        else:
            row["counts"] = [{"key": o["key"], "label": o["label"],
                              "count": sum(1 for g in given if g == o["key"])}
                             for o in FIXED_OPTIONS[q.kind]]
            if q.kind == "rating" and given:
                row["average"] = round(sum(int(g) for g in given) / len(given), 1)
        out.append(row)
    return out
