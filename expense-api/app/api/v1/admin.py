"""OA data-maintenance admin endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from app.admin import service
from app.admin.registry import REGISTRY
from app.core.deps import SessionDep, require_roles

router = APIRouter(prefix="/admin", tags=["data-maintenance"])

AdminUser = Annotated[dict, Depends(require_roles("system_admin"))]


class ListResponse(BaseModel):
    items: list[dict]
    total: int


class BulkDeleteRequest(BaseModel):
    ids: list[uuid.UUID]


def _actor(user: dict) -> tuple[uuid.UUID, str]:
    return uuid.UUID(user["sub"]), user.get("email", "")


@router.get("/entities")
async def list_entities(user: AdminUser):
    return [spec.schema.to_dict() | {"system": spec.system} for spec in REGISTRY.values()]


@router.get("/{entity}", response_model=ListResponse)
async def list_records(entity: str, db: SessionDep, user: AdminUser,
                       page: int = Query(1, ge=1), page_size: int = Query(20, le=200),
                       search: str | None = Query(None)):
    try:
        items, total = await service.list_records(db, entity, page=page, page_size=page_size, search=search)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return ListResponse(items=items, total=total)


@router.get("/{entity}/{record_id}")
async def get_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser):
    try:
        rec = await service.get_record(db, entity, record_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if rec is None:
        raise HTTPException(404, "Record not found")
    return rec


@router.patch("/{entity}/{record_id}")
async def edit_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser,
                      patch: dict = Body(...)):
    actor_id, email = _actor(user)
    try:
        result = await service.edit_record(db, entity, record_id, patch, actor_id=actor_id, actor_email=email)
        await db.commit()
        return result
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))


@router.delete("/{entity}/{record_id}")
async def delete_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser,
                        preview: int = Query(0)):
    actor_id, email = _actor(user)
    try:
        if preview:
            summary = await service.delete_preview(db, entity, record_id)
            return {"preview": True, "cascade": summary}
        summary = await service.delete_record(db, entity, record_id, actor_id=actor_id, actor_email=email)
        await db.commit()
        return {"preview": False, "cascade": summary}
    except ValueError as e:
        await db.rollback()
        raise HTTPException(404, str(e))


@router.post("/{entity}/bulk-delete")
async def bulk_delete(entity: str, db: SessionDep, user: AdminUser, body: BulkDeleteRequest):
    actor_id, email = _actor(user)
    try:
        summary = await service.bulk_delete(db, entity, body.ids, actor_id=actor_id, actor_email=email)
        await db.commit()
        return {"deleted": len(body.ids), "cascade": summary}
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
