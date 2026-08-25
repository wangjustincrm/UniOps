"""Shared read gate for finance payment data.

Lifted out of `app/api/v1/payments.py` (where it lived as `_authorize_read`)
so `app/api/v1/vendor_credits.py`'s `/suggest` can reuse the exact same rule
instead of duplicating it. Duplication is the failure mode this module exists
to prevent: the two gates would drift, and the one that drifted open would be
the one nobody noticed.
"""
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import _FINANCE_ROLES


async def authorize_finance_read(db: AsyncSession, user: dict) -> None:
    """Gate for the Payments hub's read surface (list / summary / export) and
    for the vendor-credit netting preview.

    Deliberately NOT `payment_execute._check_can_pay` — that bar is for
    *executing* a payment (see create_batch/execute_batch in payments.py, and
    app/api/v1/remittance.py's `_authorize`), which is stricter than needed
    to merely *view* payment history. `_FINANCE_ROLES` (app.core.deps) is
    already declared for exactly this — "who may see finance data" — but was
    never wired to any endpoint, which is how any authenticated employee
    (including OA-only users with no finance role) could hit
    GET /payments/export and download every payment the company has made.
    finance_bp / finance_manager granted as an ADDITIONAL identity role
    assignment (not the JWT's primary `role`) also qualify — same lookup
    `_check_can_pay` uses for write access, so a Finance BP assigned via
    role_management sees the same payments they can execute.

    `/vendor-credits/suggest` is gated by this too: it returns a PA's gross and
    net plus the vendor credit numbers behind the difference, which is payment
    data. The unauthenticated-ish openness Phase A gave the credit-note CRUD
    ("if you can upload an invoice you can upload a credit note") deliberately
    does not extend to it.
    """
    # Imported here rather than at module scope: app.crud.payment_execute pulls
    # in the whole model/service graph, and app.core.* must stay importable
    # from anywhere without dragging that along.
    from app.crud import payment_execute

    role = user.get("role", "")
    if role in _FINANCE_ROLES:
        return
    try:
        user_id = uuid.UUID(str(user.get("sub", "")))
    except ValueError:
        raise HTTPException(status_code=403, detail="Insufficient role to view payments")
    codes = await payment_execute._user_role_codes(db, user_id, role)
    if not codes & _FINANCE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role to view payments")
