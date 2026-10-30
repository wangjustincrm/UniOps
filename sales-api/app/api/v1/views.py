"""`/views`: each user's saved views of the lists (design §13.19)."""
from app.core.deps import get_uow
from app.platform.views import views_router
from app.services.views import SalesViewService

router = views_router(SalesViewService, uow=get_uow)
