from fastapi import APIRouter
from app.api.v1.vendors import router as vendors_router
from app.api.v1.departments import router as departments_router
from app.api.v1.cost_centers import router as cost_centers_router
from app.api.v1.parts import router as parts_router
from app.api.v1.materials import router as materials_router
from app.api.v1.users import router as users_router
from app.api.v1.companies import router as companies_router
from app.api.v1.health import router as health_router
from app.api.v1.erp_mdm import router as erp_router
from app.api.v1.tax import router as tax_router
from app.api.v1.partners import router as partners_router
from app.api.v1.uom import router as uom_router
from app.api.v1.uom_conversions import router as uom_conversions_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(vendors_router)
api_router.include_router(departments_router)
api_router.include_router(cost_centers_router)
api_router.include_router(parts_router)
api_router.include_router(materials_router)
api_router.include_router(users_router)
api_router.include_router(companies_router)
api_router.include_router(erp_router)
api_router.include_router(tax_router)
api_router.include_router(partners_router)
api_router.include_router(uom_router)
api_router.include_router(uom_conversions_router)
