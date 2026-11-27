"""Shipped (user 2026-11-27): the applied 945 rows — list, meta, suggest, CSV. Read-only; the service checks the
read permission."""
from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.services.lots import ShippedService

router = resource_router(ShippedService, uow=get_uow)
