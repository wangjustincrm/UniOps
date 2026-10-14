"""The module's three permission keys (design §9), as ready-made dependencies.

* inventory.view       — every QUANTITY (WMS, IC, IA qty, comparison)
* inventory.cost.view  — every AMOUNT (IA amount/unit price, GL amount)
* inventory.admin      — Settings writes and manual Refresh

Amount fields are stripped server-side for callers without cost.view; use
`can_view_cost(payload, db)` in the endpoint, never a frontend flag.
"""
from typing import Annotated

from fastapi import Depends

from app.core.authz import require_permission

VIEW = "inventory.view"
COST_VIEW = "inventory.cost.view"
ADMIN = "inventory.admin"

ViewDep = Annotated[dict, Depends(require_permission(VIEW))]
AdminDep = Annotated[dict, Depends(require_permission(ADMIN))]


async def can_view_cost(payload: dict, db) -> bool:
    """Whether this caller may see amounts. Same admission rule as the gate
    (system_admin short-circuits; otherwise the role union must hold the key)."""
    import uuid

    from uniops_authz.core import has_permission

    try:
        uid = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return False
    return await has_permission(db, uid, payload.get("role", ""), COST_VIEW)
