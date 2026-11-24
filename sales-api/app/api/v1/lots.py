"""Lots (traceability and recall): list, meta, suggest, CSV. Read-only; the service
checks the read permission."""
from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.services.lots import LotService

router = resource_router(LotService, uow=get_uow)
