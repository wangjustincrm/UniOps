"""Marked questions: up to ten per person, re-asked from the panel with one click.

Only ever the caller's own list. There is no user id in any request — it comes
from the token — so there is nothing to change to read or delete someone
else's. A saved question is just text; asking it again goes through /chat and
its scope like any typed question, so keeping one grants nothing.
"""
import uuid as _uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUserPayload, SessionDep
from app.models.assistant_saved_question import (
    MAX_SAVED_QUESTIONS, AssistantSavedQuestion,
)

router = APIRouter(prefix="/assistant/saved", tags=["assistant"])


class SaveRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def _me(user: dict) -> _uuid.UUID:
    sub = user.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Not signed in")
    return _uuid.UUID(str(sub))


def _out(row: AssistantSavedQuestion) -> dict:
    return {"id": str(row.id), "question": row.question,
            "created_at": row.created_at.isoformat() if row.created_at else None}


async def _list(db, me) -> list[dict]:
    rows = (await db.execute(
        select(AssistantSavedQuestion)
        .where(AssistantSavedQuestion.user_id == me)
        .order_by(AssistantSavedQuestion.created_at.desc())
    )).scalars().all()
    return [_out(r) for r in rows]


@router.get("")
async def list_saved(db: SessionDep, user: CurrentUserPayload) -> dict:
    return {"items": await _list(db, _me(user)), "limit": MAX_SAVED_QUESTIONS}


@router.post("")
async def save(body: SaveRequest, db: SessionDep, user: CurrentUserPayload) -> dict:
    me = _me(user)
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question is empty")

    existing = (await db.execute(
        select(AssistantSavedQuestion).where(
            AssistantSavedQuestion.user_id == me,
            AssistantSavedQuestion.question == question,
        ))).scalar_one_or_none()
    if existing is None:
        count = (await db.execute(
            select(func.count()).select_from(AssistantSavedQuestion)
            .where(AssistantSavedQuestion.user_id == me))).scalar_one()
        # Refuse rather than push the oldest out: each of these was marked on
        # purpose, and dropping one silently is losing something the person
        # chose to keep. They pick what goes.
        if count >= MAX_SAVED_QUESTIONS:
            raise HTTPException(
                status_code=409,
                detail=f"You can keep up to {MAX_SAVED_QUESTIONS} marked questions. "
                       "Unmark one first.")
        db.add(AssistantSavedQuestion(user_id=me, question=question))
        try:
            await db.commit()
        except IntegrityError:
            # The same question marked from two tabs at once: the other one won,
            # and the outcome is the same.
            await db.rollback()
    # Marking the same question twice is not an error, just already done.
    return {"items": await _list(db, me), "limit": MAX_SAVED_QUESTIONS}


@router.delete("/{saved_id}")
async def unsave(saved_id: _uuid.UUID, db: SessionDep, user: CurrentUserPayload) -> dict:
    me = _me(user)
    # Scoped by owner in the statement itself: someone else's id deletes nothing.
    await db.execute(delete(AssistantSavedQuestion).where(
        AssistantSavedQuestion.id == saved_id,
        AssistantSavedQuestion.user_id == me,
    ))
    await db.commit()
    return {"items": await _list(db, me), "limit": MAX_SAVED_QUESTIONS}
