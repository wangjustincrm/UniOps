from fastapi import APIRouter

from app.api.v1 import health, operations, partners

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(partners.router)
for r in (operations.connections, operations.documents, operations.runs, operations.log):
    api_router.include_router(r)
