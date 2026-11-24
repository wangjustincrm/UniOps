"""Configuration → Items, Partner codes, DC routing (P3). Permissions are
checked by the services."""
from typing import Annotated, Any

from fastapi import Depends

from app.core.deps import get_uow
from app.models.config import Carrier, DcRouting, Item, ItemPartnerCode, Warehouse
from app.platform.crud_router import resource_router
from app.schemas.audit import AuditEntryRead
from app.services import audit
from app.services.items import READ, DcRoutingService, ItemService, PartnerCodeService
from app.services.carriers import CarrierService
from app.services.warehouses import WarehouseService

UoW = Annotated[Any, Depends(get_uow)]

items = resource_router(ItemService, uow=get_uow)
partner_codes = resource_router(PartnerCodeService, uow=get_uow)
dc_routing = resource_router(DcRoutingService, uow=get_uow)
warehouses = resource_router(WarehouseService, uow=get_uow)
carriers = resource_router(CarrierService, uow=get_uow)


@carriers.get("/{id}/audit", response_model=list[AuditEntryRead])
async def carrier_audit(u: UoW, id: int):
    return await audit.history(u, Carrier, id, permission=READ)


@warehouses.get("/{id}/audit", response_model=list[AuditEntryRead])
async def warehouse_audit(u: UoW, id: int):
    return await audit.history(u, Warehouse, id, permission=READ)


@items.get("/{id}/audit", response_model=list[AuditEntryRead])
async def item_audit(u: UoW, id: int):
    return await audit.history(u, Item, id, permission=READ)


@partner_codes.get("/{id}/audit", response_model=list[AuditEntryRead])
async def partner_code_audit(u: UoW, id: int):
    return await audit.history(u, ItemPartnerCode, id, permission=READ)


@dc_routing.get("/{id}/audit", response_model=list[AuditEntryRead])
async def dc_routing_audit(u: UoW, id: int):
    return await audit.history(u, DcRouting, id, permission=READ)
