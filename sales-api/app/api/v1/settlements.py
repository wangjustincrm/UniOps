"""`/settlements`: the deposits the payment gateways paid out (read-only; CSV; its PDFs are declared on the list)."""
from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.services.settlement_list import SettlementRowService

router = resource_router(SettlementRowService, uow=get_uow)
