from fastapi import APIRouter

from app.api.v1 import health, item_master, operations, orders, partners, shadow, views

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(partners.router)
for r in (operations.connections, operations.edi_settings, operations.shadow_setting, operations.documents, operations.runs, operations.log,
          item_master.items, item_master.partner_codes, item_master.dc_routing, orders.router, shadow.router, views.router):
    api_router.include_router(r)
