"""Configuration → Partners. Permissions are checked by the service."""
from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.services.partners import PartnerService

router = resource_router(PartnerService, uow=get_uow)
