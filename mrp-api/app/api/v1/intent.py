import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from app.core.authz import require_permission
from app.core.deps import BearerToken, SessionDep
from app.models.intent import MrpIntentProduct
from app.services.intent_products import (
    IntentBindConflict,
    bind_intent_to_material,
    generate_intent_code,
)
# Imported as a bare name (not accessed via the identity_client module) so
# tests can `monkeypatch.setattr(intent, "resolve_current_user_name", ...)`
# — same idiom app/api/v1/series.py uses for its own PUT /series/cells.
from app.services.identity_client import resolve_current_user_name

router = APIRouter(prefix="/intent-products", tags=["intent-products"])

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
WriteDep = Annotated[dict, Depends(require_permission("mrp.demand.write"))]


class IntentProductCreate(BaseModel):
    name: str
    note: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class IntentProductResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    note: str | None
    status: str
    bound_material_code: str | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[IntentProductResponse])
async def list_intent_products(
    db: SessionDep, _: ReadDep,
    status_filter: str = Query("active", alias="status"),
):
    stmt = select(MrpIntentProduct).order_by(MrpIntentProduct.created_at.desc())
    if status_filter != "all":
        stmt = stmt.where(MrpIntentProduct.status == status_filter)
    return (await db.execute(stmt)).scalars().all()


@router.post("", response_model=IntentProductResponse, status_code=status.HTTP_201_CREATED)
async def create_intent_product(body: IntentProductCreate, db: SessionDep, payload: WriteDep):
    row = MrpIntentProduct(
        code=generate_intent_code(), name=body.name, note=body.note,
        status="active", created_by=uuid.UUID(payload["sub"]),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


class IntentBindRequest(BaseModel):
    material_code: str


class IntentBindResponse(BaseModel):
    intent: IntentProductResponse
    moved_months: int
    moved_qty: Decimal


@router.post("/{intent_id}/bind", response_model=IntentBindResponse)
async def bind_intent_product(
    intent_id: uuid.UUID, body: IntentBindRequest, db: SessionDep, payload: WriteDep,
    token: BearerToken,
):
    row = await db.get(MrpIntentProduct, intent_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "intent product not found")
    if row.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"intent product is {row.status}, only active ones can be bound")
    actor_id = uuid.UUID(payload["sub"])
    # Once per request, same never-raises-degrades-to-None idiom
    # app/api/v1/series.py's PUT /series/cells uses for its own change-log
    # rows — a name lookup must never break the bind. Blocking sync
    # httpx.Client call, so it runs off the event loop via
    # anyio.to_thread.run_sync (see identity_client.py's docstring).
    actor_name = await anyio.to_thread.run_sync(resolve_current_user_name, token)
    try:
        moved_months, moved_qty = await bind_intent_to_material(
            db, intent_code=row.code, material_code=body.material_code,
            actor_id=actor_id, actor_name=actor_name,
        )
    except IntentBindConflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{body.material_code} already has forecast rows — merge them by hand first",
        )
    row.status = "bound"
    row.bound_material_code = body.material_code
    row.bound_at = datetime.now(timezone.utc)
    row.bound_by = actor_id
    await db.commit()
    await db.refresh(row)
    return IntentBindResponse(intent=row, moved_months=moved_months, moved_qty=moved_qty)


@router.post("/{intent_id}/drop", response_model=IntentProductResponse)
async def drop_intent_product(intent_id: uuid.UUID, db: SessionDep, _: WriteDep):
    row = await db.get(MrpIntentProduct, intent_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "intent product not found")
    if row.status == "bound":
        raise HTTPException(status.HTTP_409_CONFLICT, "a bound intent product cannot be dropped")
    row.status = "dropped"
    await db.commit()
    await db.refresh(row)
    return row
