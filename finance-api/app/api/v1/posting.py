"""Posting events query API — read-only window into the accounting spine."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.posting import PostingEvent, PostingLine
from app.schemas.posting import PostingEventOut, PostingLineOut

router = APIRouter(prefix="/posting", tags=["posting"])


@router.get("/events", response_model=list[PostingEventOut])
async def list_events(
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
    source_doc_type: str | None = Query(default=None),
    source_doc_id: uuid.UUID | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    q = select(PostingEvent).order_by(PostingEvent.occurred_at.desc()).limit(limit)
    if source_doc_type:
        q = q.where(PostingEvent.source_doc_type == source_doc_type)
    if source_doc_id:
        q = q.where(PostingEvent.source_doc_id == source_doc_id)
    if event_type:
        q = q.where(PostingEvent.event_type == event_type)
    events = (await db.execute(q)).scalars().all()
    if not events:
        return []
    lines = (await db.execute(
        select(PostingLine)
        .where(PostingLine.event_id.in_([e.id for e in events]))
        .order_by(PostingLine.line_no)
    )).scalars().all()
    by_event: dict[uuid.UUID, list[PostingLine]] = {}
    for ln in lines:
        by_event.setdefault(ln.event_id, []).append(ln)
    return [
        PostingEventOut(
            **{c: getattr(e, c) for c in (
                "id", "source_service", "source_doc_type", "source_doc_id",
                "source_doc_number", "event_type", "occurred_at", "status")},
            lines=[PostingLineOut.model_validate(ln) for ln in by_event.get(e.id, [])],
        )
        for e in events
    ]
