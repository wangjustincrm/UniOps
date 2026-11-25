"""Operations → Audit log (sales36): the logs (list, meta, one with its actions and entity changes) and the entity
changes (list, meta). sales.config for now."""
import uuid
from typing import Annotated, Any

from fastapi import Depends

from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.platform.routes import Router
from app.schemas.audit_logs import AuditLogDetail
from app.services.audit_logs import AuditLogService, EntityChangeService

UoW = Annotated[Any, Depends(get_uow)]

detail = Router(prefix="/audit-logs", tags=["audit"])


@detail.get("/{id}/detail", response_model=AuditLogDetail)
async def audit_log_detail(u: UoW, id: uuid.UUID):
    return await AuditLogService(u).detail(id)


logs = resource_router(AuditLogService, uow=get_uow)
changes = resource_router(EntityChangeService, uow=get_uow)
