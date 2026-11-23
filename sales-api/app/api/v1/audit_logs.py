"""Operations → Audit log (sales36): the logs (list, meta, one with its actions and entity changes) and the entity
changes (list, meta). sales.config for now."""
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.deps import get_uow
from app.platform.crud_router import read_router
from app.schemas.audit_logs import AuditLogDetail
from app.services.audit_logs import AuditLogService, EntityChangeService

UoW = Annotated[Any, Depends(get_uow)]

detail = APIRouter(prefix="/audit-logs", tags=["audit"])


@detail.get("/{id}/detail", response_model=AuditLogDetail)
async def audit_log_detail(u: UoW, id: uuid.UUID):
    return await AuditLogService(u).detail(id)


logs = read_router("/audit-logs", AuditLogService, uow=get_uow, tags=["audit"])
changes = read_router("/entity-changes", EntityChangeService, uow=get_uow, tags=["audit"])
