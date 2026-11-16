"""`/payments`: the Payments list (Shopify payment and refund transactions, read-only; CSV)."""
from app.core.deps import get_uow
from app.platform.crud_router import read_router
from app.services.payment_list import PaymentRowService

router = read_router("/payments", PaymentRowService, uow=get_uow, tags=["payments"], export=True)
