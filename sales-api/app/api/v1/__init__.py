from fastapi import APIRouter

from app.api.v1 import health, invoices, item_master, lots, notifications, operations, orders, partners, shadow, transfers, views

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(partners.router)
for r in (operations.connections, operations.edi_settings, operations.shadow_setting, operations.scheduler_setting, operations.documents, operations.runs, operations.log,
          item_master.items, item_master.partner_codes, item_master.dc_routing, item_master.warehouses, orders.router, invoices.router, lots.router, transfers.router, shadow.router, views.router,
          notifications.rules, notifications.ops, notifications.history):
    api_router.include_router(r)
