"""Configuration → Partners: the platform's crud_router plus the audit
history of one partner. Permissions are checked by the services."""
from typing import Annotated, Any

from fastapi import Depends

from app.core.deps import get_uow
from app.models.config import Partner
from app.platform.crud_router import crud_router
from app.schemas.audit import AuditEntryRead
from app.services import audit
from app.services.partners import READ, PartnerService

router = crud_router("/config/partners", PartnerService, uow=get_uow, tags=["partners"])


@router.get("/{id}/audit", response_model=list[AuditEntryRead])
async def partner_audit(u: Annotated[Any, Depends(get_uow)], id: int):
    return await audit.history(u, Partner, id, permission=READ)
