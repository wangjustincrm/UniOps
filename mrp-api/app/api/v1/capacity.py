"""Capacity rules CRUD (Phase 1B Task 1, design §6.4).

Plain master-data CRUD over `mrp_capacity_rules` — same shape as
app/api/v1/consignment.py's stock endpoints (require_permission deps, a
`_get_..._or_404` helper, Pydantic Create/Update/Response schemas). GET is
gated by `mrp.report.view`; POST/PATCH/DELETE by `mrp.param.write` (the same
key admin_sync's `/admin/wms-sync` uses — see
tests/test_permission_gates.py).

`limit_value` is Numeric(18,3); Pydantic (de)serializes Decimal as a JSON
string, never a float, so callers must always send/receive it quoted (e.g.
`"12"` in, `"12.000"` out) — see feedback_uniops_decimal_as_string.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.models.capacity import MrpCapacityRule

router = APIRouter(prefix="/capacity", tags=["capacity"])

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
WriteDep = Annotated[dict, Depends(require_permission("mrp.param.write"))]


# ── Schemas ──────────────────────────────────────────────────────────────


class CapacityRuleCreate(BaseModel):
    scope_type: str
    scope_ref: str | None = None
    constraint_type: str
    limit_value: Decimal
    uom: str | None = None
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True


class CapacityRuleUpdate(BaseModel):
    scope_type: str | None = None
    scope_ref: str | None = None
    constraint_type: str | None = None
    limit_value: Decimal | None = None
    uom: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    is_active: bool | None = None


class CapacityRuleResponse(BaseModel):
    id: uuid.UUID
    scope_type: str
    scope_ref: str | None
    constraint_type: str
    limit_value: Decimal
    uom: str | None
    effective_from: date
    effective_to: date | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Helpers ──────────────────────────────────────────────────────────────


async def _get_rule_or_404(db: SessionDep, rule_id: uuid.UUID) -> MrpCapacityRule:
    row = await db.get(MrpCapacityRule, rule_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capacity rule not found")
    return row


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/rules", response_model=list[CapacityRuleResponse])
async def list_rules(db: SessionDep, _: ReadDep):
    rows = (await db.execute(select(MrpCapacityRule).order_by(MrpCapacityRule.created_at))).scalars().all()
    return rows


@router.post("/rules", response_model=CapacityRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(body: CapacityRuleCreate, db: SessionDep, _: WriteDep):
    row = MrpCapacityRule(**body.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.patch("/rules/{rule_id}", response_model=CapacityRuleResponse)
async def update_rule(rule_id: uuid.UUID, body: CapacityRuleUpdate, db: SessionDep, _: WriteDep):
    row = await _get_rule_or_404(db, rule_id)
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: uuid.UUID, db: SessionDep, _: WriteDep):
    row = await _get_rule_or_404(db, rule_id)
    await db.delete(row)
    await db.commit()
