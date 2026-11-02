"""Saved views of the Sales lists (design §13.19): the platform's ViewService
on `sales_user_views`, for the lists below — keyed by each list's path, as
the client knows it."""
from __future__ import annotations

from app.models.records import UserView
from app.platform.views import ViewService
from app.services.connections import ConnectionService
from app.services.items import DcRoutingService, ItemService, PartnerCodeService
from app.services.operations import DocumentService, LogService, RunService
from app.services.order_views import OrderService
from app.services.partners import PartnerService
from app.services.invoice_views import InvoiceService
from app.services.shadow import ShadowMatchService
from app.services.transfer_views import TransferService
from app.services.warehouses import WarehouseService

LISTS = {
    "config/partners": PartnerService, "config/items": ItemService, "config/partner-codes": PartnerCodeService,
    "config/dc-routing": DcRoutingService, "config/connections": ConnectionService, "orders": OrderService,
    "documents": DocumentService, "runs": RunService, "log": LogService, "shadow-matches": ShadowMatchService,
    "invoices": InvoiceService, "config/warehouses": WarehouseService, "transfers": TransferService,
}


class SalesViewService(ViewService):
    model = UserView
    lists = LISTS
