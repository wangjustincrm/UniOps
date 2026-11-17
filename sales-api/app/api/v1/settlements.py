"""`/settlements`: the deposits the payment gateways paid out (read-only; CSV)."""
from app.core.deps import get_uow
from app.platform.crud_router import read_router
from app.services.settlement_list import SettlementRowService

router = read_router("/settlements", SettlementRowService, uow=get_uow, tags=["settlements"], export=True)
