"""HSE news: posted by HSE, read by everybody on My Actions (round 10)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanManageSettings, CanReportIncident
from app.models.mirrors import User
from app.models.news import NewsItem

router = APIRouter(prefix="/news")


class NewsIn(BaseModel):
    importance: str = Field(pattern="^(high|medium|low)$")
    message: str = Field(min_length=1, max_length=2000)


class NewsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    posted_at: datetime
    importance: str
    message: str
    posted_by_name: str | None


@router.get("", response_model=list[NewsOut])
async def current_news(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """What is up now, newest first — for the ticker everybody sees."""
    return list((await db.execute(
        select(NewsItem).where(NewsItem.archived_at.is_(None))
        .order_by(NewsItem.posted_at.desc()).limit(20))).scalars())


@router.post("", response_model=NewsOut, status_code=status.HTTP_201_CREATED)
async def post_news(payload: NewsIn, db: SessionDep, user: CanManageSettings):
    me = await db.get(User, _user_id(user))
    row = NewsItem(id=uuid.uuid4(), posted_at=datetime.now(timezone.utc),
                   importance=payload.importance, message=payload.message.strip(),
                   posted_by=_user_id(user), posted_by_name=me.full_name if me else None)
    if not row.message:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A news item needs a message")
    db.add(row)
    await db.flush()
    return row


@router.post("/{news_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
async def take_down(news_id: uuid.UUID, db: SessionDep, user: CanManageSettings):
    """Off the ticker; kept, with who took it down."""
    row = await db.get(NewsItem, news_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such news item")
    if row.archived_at is None:
        me = await db.get(User, _user_id(user))
        row.archived_at = datetime.now(timezone.utc)
        row.archived_by_name = me.full_name if me else None
        await db.flush()
