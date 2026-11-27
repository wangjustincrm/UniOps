from fastapi import APIRouter

from app.api.v1 import attachments, audit_logs, health, help_texts, inventory, invoices, item_master, lots, notifications, operations, orders, partners, payments, settlements, shipped, transfers, views

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(partners.router)
for r in (audit_logs.detail, audit_logs.logs, audit_logs.changes):  # detail before /{id}
    api_router.include_router(r)
for r in (operations.connections, operations.edi_settings, operations.manual_order_settings, operations.log_levels, operations.features, operations.scheduler_setting, operations.documents, operations.runs, operations.log,
          item_master.items, item_master.partner_codes, item_master.dc_routing, item_master.warehouses, item_master.carriers, orders.router, invoices.router, payments.router, settlements.router, lots.router, shipped.router, inventory.router, attachments.router, transfers.router, views.router, help_texts.router,
          notifications.rules, notifications.ops, notifications.history):
    api_router.include_router(r)
