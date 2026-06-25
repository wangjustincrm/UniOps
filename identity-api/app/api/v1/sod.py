"""SoD rules — read/toggle API. Enforcement happens at chokepoints
(finance payment executor reads the shared sod_rules table directly)."""
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import CurrentUserPayload, SessionDep
from app.models.audit import AuditLog
from app.models.sod import SodRule

router = APIRouter(prefix="/sod", tags=["sod"])

_MANAGE_ROLES = {"system_admin", "finance_manager"}


class SodRuleOut(BaseModel):
    id: uuid.UUID
    rule_code: str
    name: str
    description: str | None
    enabled: bool

    model_config = {"from_attributes": True}


class SodToggleRequest(BaseModel):
    enabled: bool
    reason: str | None = None


@router.get("/rules", response_model=list[SodRuleOut])
async def list_rules(payload: CurrentUserPayload, db: SessionDep):
    return (await db.execute(select(SodRule).order_by(SodRule.rule_code))).scalars().all()


@router.patch("/rules/{rule_code}", response_model=SodRuleOut)
async def toggle_rule(rule_code: str, body: SodToggleRequest,
                      payload: CurrentUserPayload, db: SessionDep):
    if payload.get("role") not in _MANAGE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Insufficient role to manage SoD rules")
    rule = (await db.execute(
        select(SodRule).where(SodRule.rule_code == rule_code)
    )).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="SoD rule not found")
    rule.enabled = body.enabled
    db.add(AuditLog(
        service="identity", action="sod_rule_toggled",
        actor_id=uuid.UUID(payload["sub"]),
        object_type="sod_rule", object_id=rule.id,
        detail={"rule_code": rule_code, "enabled": body.enabled, "reason": body.reason},
    ))
    await db.flush()
    return rule
