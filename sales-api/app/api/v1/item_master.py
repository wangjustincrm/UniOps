"""Configuration → Items, Partner codes, DC routing (P3). Permissions are
checked by the services."""
from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.services.items import DcRoutingService, ItemService, PartnerCodeService
from app.services.carriers import CarrierService
from app.services.warehouses import WarehouseService

items = resource_router(ItemService, uow=get_uow)
partner_codes = resource_router(PartnerCodeService, uow=get_uow)
dc_routing = resource_router(DcRoutingService, uow=get_uow)
warehouses = resource_router(WarehouseService, uow=get_uow)
carriers = resource_router(CarrierService, uow=get_uow)
