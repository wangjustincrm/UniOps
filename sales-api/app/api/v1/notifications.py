"""Configuration → Notifications (FR-OB-56), Operations Health and the
dashboard (FR-OB-52), and where a Task Inbox task opens."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.config import settings
from app.core.deps import get_uow
from app.models.config import NotificationRule
from app.platform.crud_router import crud_router
from app.platform.query import QuerySpec, query_spec
from app.runtime import Runtime, get_runtime
from app.schemas.audit import AuditEntryRead
from app.schemas.health import DashboardRead, HealthRead, InboxTarget
from app.schemas.notifications import EventTypeRead, GateEventRead, RuleTestResult, UserOption
from app.services import audit
from app.services.health import HealthService
from app.services.inbox import InboxService
from app.services.notifications import READ, NotificationRuleService, NotificationService

UoW = Annotated[Any, Depends(get_uow)]
Rt = Annotated[Runtime, Depends(get_runtime)]
Spec = Annotated[QuerySpec, Depends(query_spec)]

rules = crud_router("/config/notification-rules", NotificationRuleService, uow=get_uow, tags=["notifications"])


@rules.get("/{id}/audit", response_model=list[AuditEntryRead])
async def rule_audit(u: UoW, id: int):
    return await audit.history(u, NotificationRule, id, permission=READ)


@rules.post("/{id}/test", response_model=RuleTestResult)
async def rule_test(u: UoW, rt: Rt, id: int):
    out = await NotificationRuleService(u).send_test(id, rt.tasks)
    await u.commit()
    return out


ops = APIRouter(tags=["operations"])


@ops.get("/config/notification-events", response_model=list[EventTypeRead])
async def notification_events(u: UoW):
    return await NotificationRuleService(u).events()


@ops.get("/config/notification-users", response_model=list[UserOption])
async def notification_users(u: UoW, q: str = ""):
    return await NotificationRuleService(u).users(q)


@ops.get("/config/notification-gate", response_model=list[GateEventRead])
async def notification_gate(u: UoW):
    return await NotificationRuleService(u).gate()


history = APIRouter(prefix="/notifications", tags=["notifications"])


@history.get("/meta")
async def history_meta(u: UoW):
    return await NotificationService(u).meta()


@history.get("")
async def history_list(u: UoW, spec: Spec):
    return await NotificationService(u).list(spec)


@ops.get("/operations/health", response_model=HealthRead)
async def operations_health(u: UoW):
    return await HealthService(u).health(settings)


@ops.get("/operations/dashboard", response_model=DashboardRead)
async def operations_dashboard(u: UoW):
    return await HealthService(u).dashboard()


@ops.get("/inbox/{key}", response_model=InboxTarget)
async def inbox_target(u: UoW, key: str):
    return await InboxService(u).resolve(key)
