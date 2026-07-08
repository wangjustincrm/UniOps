from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.admin_rooms import router as admin_rooms_router
from app.api.v1.admin_config import router as admin_config_router
from app.api.v1.admin_bookings import router as admin_bookings_router
from app.api.v1.admin_notifications import router as admin_notifications_router
from app.api.v1.rooms import router as rooms_router
from app.api.v1.precheck import router as precheck_router
from app.api.v1.bookings import router as bookings_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(admin_rooms_router, prefix="/admin/rooms", tags=["admin"])
api_router.include_router(admin_config_router, prefix="/admin/config", tags=["admin"])
# /admin/bookings — static prefix, registered before any parameterized siblings
api_router.include_router(admin_bookings_router, prefix="/admin/bookings", tags=["admin"])
# /admin/notifications — Task 10 notification log monitor + resend
api_router.include_router(admin_notifications_router, prefix="/admin/notifications", tags=["admin"])
api_router.include_router(rooms_router, prefix="/rooms", tags=["rooms"])
# /bookings/precheck — static path, MUST be registered before /bookings (Task 7 routes)
# so that "precheck" is not mistaken for a booking ID parameter.
api_router.include_router(precheck_router, prefix="/bookings", tags=["bookings"])
api_router.include_router(bookings_router, prefix="/bookings", tags=["bookings"])
