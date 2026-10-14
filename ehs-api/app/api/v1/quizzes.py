"""Training quizzes.

Two response shapes for the same question row, and the difference between them
is the security boundary of this whole feature:

* `QuestionForLearner` — prompt and options. No `correct`, no `explanation`.
* `QuestionForAuthor` — everything, behind `ehs.training.write`.

They are separate classes rather than one class with a flag because a flag gets
passed wrong once and the answer key ships to the browser. Two shapes cannot be
got wrong by accident.

Taking a quiz is gated on the key every role holds — the whole point is that
somebody scans a code at the end of a session and answers on their own phone.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanReadTraining, CanReportIncident, CanWriteTraining
from app.crud import quiz as crud

router = APIRouter()


class Option(BaseModel):
    key: str = Field(min_length=1, max_length=8)
    label: str = Field(min_length=1, max_length=300)


class QuestionIn(BaseModel):
    prompt: str = Field(min_length=1)
    kind: str = Field(default="single", pattern="^(single|multi|true_false)$")
    options: list[Option] = Field(min_length=2, max_length=8)
    correct: list[str] = Field(min_length=1)
    explanation: str | None = None
    points: int = Field(default=1, ge=1, le=10)


class QuestionsIn(BaseModel):
    questions: list[QuestionIn] = Field(min_length=1, max_length=50)


class QuestionForLearner(BaseModel):
    """What somebody taking the quiz receives. No answer key — see the module
    docstring; this is the security boundary of the feature."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    prompt: str
    kind: str
    options: list
    points: int


class QuestionForAuthor(QuestionForLearner):
    correct: list
    explanation: str | None


class QuizIn(BaseModel):
    course_id: uuid.UUID | None = None
    # Round 6: or the document it tests — signing asks for a pass first.
    document_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    intro: str | None = None
    pass_mark_percent: int = Field(default=80, ge=1, le=100)
    max_attempts: int | None = Field(default=None, ge=1, le=20)


class QuizOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID | None
    document_id: uuid.UUID | None = None
    title: str
    intro: str | None
    pass_mark_percent: int
    max_attempts: int | None
    is_active: bool
    qr_token: str | None
    question_count: int = 0


class AttemptIn(BaseModel):
    # {question_id: [option keys]}
    answers: dict[str, list[str]] = Field(default_factory=dict)
    delivery_id: uuid.UUID | None = None


class SessionChoice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    delivery_no: str
    title: str
    session_date: date
    start_time: time | None
    end_time: time | None
    location_text: str | None


class JoinIn(BaseModel):
    # Only when the person picked one of several sessions running at once.
    delivery_id: uuid.UUID | None = None


class JoinOut(BaseModel):
    # The session whose roster they are on, when there is one.
    joined: SessionChoice | None
    # True only the first time — the page says "you have been added" once.
    added: bool
    # More than one session under way: the person says which.
    choices: list[SessionChoice]
    note: str | None


class MarkedQuestion(BaseModel):
    question_id: str
    prompt: str
    correct: bool
    your_answer: list[str]
    right_answer: list[str]
    explanation: str | None


class AttemptOut(BaseModel):
    id: uuid.UUID
    score_percent: Decimal
    points_scored: int
    points_possible: int
    passed: bool
    pass_mark_percent: int
    submitted_at: datetime
    training_record_id: uuid.UUID | None
    # The session the attempt counted at, if any — the page says what a pass
    # means from it: a session's roster, or nothing towards a certificate.
    delivery_id: uuid.UUID | None = None
    # Shown straight after submitting. A wrong answer with no explanation
    # teaches nothing, which would make the quiz a filter rather than training.
    marked: list[MarkedQuestion]


class AttemptSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str
    submitted_at: datetime
    score_percent: Decimal
    passed: bool


class QuestionStat(BaseModel):
    question_id: str
    prompt: str
    got_it_wrong: int
    of: int


class ResultsOut(BaseModel):
    people: int
    passed: int
    pass_rate: float | None
    average_score: float | None
    total_attempts: int
    by_question: list[QuestionStat]


async def _quiz_out(db, quiz) -> QuizOut:
    item = QuizOut.model_validate(quiz)
    item.question_count = len(await crud.questions_for(db, quiz.id))
    return item


@router.get("/quizzes", response_model=list[QuizOut])
async def list_quizzes(
    db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    course_id: uuid.UUID | None = None,
):
    return [await _quiz_out(db, q) for q in await crud.list_quizzes(db, course_id=course_id)]


@router.post("/quizzes", response_model=QuizOut, status_code=status.HTTP_201_CREATED)
async def create_quiz(payload: QuizIn, db: SessionDep, user: CanWriteTraining):
    quiz = await crud.create_quiz(db, payload.model_dump(), creator_id=_user_id(user))
    return await _quiz_out(db, quiz)


@router.get("/quizzes/{quiz_id}", response_model=QuizOut)
async def get_quiz(quiz_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    return await _quiz_out(db, await crud.get_quiz(db, quiz_id))


@router.post("/quizzes/{quiz_id}/retire", response_model=QuizOut)
async def retire_quiz(quiz_id: uuid.UUID, db: SessionDep, user: CanWriteTraining):  # noqa: ARG001
    """Take a quiz out of use — a document or a course stops asking for it.

    ★ Round-6 review: there was no way to. A quiz put on a document by mistake
    held its signing until somebody wrote questions for it.
    """
    return await _quiz_out(db, await crud.retire(db, await crud.get_quiz(db, quiz_id)))


@router.get("/quizzes/{quiz_id}/questions", response_model=list[QuestionForLearner])
async def get_questions(quiz_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """What a learner sees. Deliberately without the answers. Refused once the
    quiz is retired, so the page says so before anybody answers it."""
    crud.require_active(await crud.get_quiz(db, quiz_id))
    return await crud.questions_for(db, quiz_id)


@router.get("/quizzes/{quiz_id}/answer-key", response_model=list[QuestionForAuthor])
async def get_answer_key(quiz_id: uuid.UUID, db: SessionDep, user: CanWriteTraining):  # noqa: ARG001
    """The authoring view. Named so nobody wires it into the learner's page."""
    await crud.get_quiz(db, quiz_id)
    return await crud.questions_for(db, quiz_id)


@router.put("/quizzes/{quiz_id}/questions", response_model=list[QuestionForAuthor])
async def put_questions(
    quiz_id: uuid.UUID, payload: QuestionsIn, db: SessionDep, user: CanWriteTraining,  # noqa: ARG001
):
    """Refused once anybody has taken it — see the crud for why."""
    quiz = await crud.get_quiz(db, quiz_id)
    return await crud.replace_questions(
        db, quiz, [q.model_dump() for q in payload.questions])


@router.post("/quizzes/{quiz_id}/join", response_model=JoinOut)
async def join_session(
    quiz_id: uuid.UUID, payload: JoinIn, db: SessionDep, user: CanReportIncident,
):
    """Scanning the code puts you on the roster of the session it was shown at.

    Round 5, answer box 4. Called by the quiz page as it opens, so attendance is
    recorded whether or not the quiz is then passed.
    """
    quiz = await crud.get_quiz(db, quiz_id)
    got = await crud.join(db, quiz, _user_id(user), delivery_id=payload.delivery_id)
    return JoinOut(
        joined=SessionChoice.model_validate(got.joined) if got.joined else None,
        added=got.added,
        choices=[SessionChoice.model_validate(d) for d in got.choices],
        note=got.note,
    )


@router.post("/quizzes/{quiz_id}/attempts", response_model=AttemptOut,
             status_code=status.HTTP_201_CREATED)
async def take_quiz(
    quiz_id: uuid.UUID, payload: AttemptIn, db: SessionDep, user: CanReportIncident,
):
    """Answer it. Marked on the server; a pass writes the training record."""
    quiz = await crud.get_quiz(db, quiz_id)
    attempt, marked = await crud.score(
        db, quiz, payload.answers, user_id=_user_id(user),
        delivery_id=payload.delivery_id)
    return AttemptOut(
        id=attempt.id,
        score_percent=attempt.score_percent,
        points_scored=attempt.points_scored,
        points_possible=attempt.points_possible,
        passed=attempt.passed,
        pass_mark_percent=quiz.pass_mark_percent,
        submitted_at=attempt.submitted_at,
        training_record_id=attempt.training_record_id,
        delivery_id=attempt.delivery_id,
        marked=[MarkedQuestion(**m) for m in marked],
    )


@router.get("/quizzes/{quiz_id}/attempts", response_model=list[AttemptSummary])
async def list_attempts(
    quiz_id: uuid.UUID, db: SessionDep, user: CanReadTraining,  # noqa: ARG001
):
    await crud.get_quiz(db, quiz_id)
    return await crud.attempts_for(db, quiz_id)


@router.get("/quizzes/{quiz_id}/mine", response_model=list[AttemptSummary])
async def my_attempts(quiz_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    """My own goes at it — visible without the training-read permission,
    because everybody can see their own result."""
    await crud.get_quiz(db, quiz_id)
    return await crud.attempts_for(db, quiz_id, user_id=_user_id(user))


@router.get("/quizzes/{quiz_id}/results", response_model=ResultsOut)
async def quiz_results(quiz_id: uuid.UUID, db: SessionDep, user: CanReadTraining):  # noqa: ARG001
    """How the room did, and which question they got wrong — the useful half.
    A quiz everybody failed on question four has found a gap in the training."""
    await crud.get_quiz(db, quiz_id)
    return await crud.results(db, quiz_id)
