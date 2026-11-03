"""Configuration → Items, Partner codes, DC routing (P3). Permissions are
checked by the services."""
from typing import Annotated, Any

from fastapi import Depends

from app.core.deps import get_uow
from app.models.config import Carrier, DcRouting, Item, ItemPartnerCode, Warehouse
from app.platform.crud_router import crud_router
from app.schemas.audit import AuditEntryRead
from app.services import audit
from app.services.items import READ, DcRoutingService, ItemService, PartnerCodeService
from app.services.carriers import CarrierService
from app.services.warehouses import WarehouseService

UoW = Annotated[Any, Depends(get_uow)]

items = crud_router("/config/items", ItemService, uow=get_uow, tags=["items"])
partner_codes = crud_router("/config/partner-codes", PartnerCodeService, uow=get_uow, tags=["partner-codes"])
dc_routing = crud_router("/config/dc-routing", DcRoutingService, uow=get_uow, tags=["dc-routing"])
warehouses = crud_router("/config/warehouses", WarehouseService, uow=get_uow, tags=["warehouses"])
carriers = crud_router("/config/carriers", CarrierService, uow=get_uow, tags=["carriers"])


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
