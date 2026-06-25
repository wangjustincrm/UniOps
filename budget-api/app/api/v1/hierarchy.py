"""Hierarchy endpoint — returns CC → L1 → Account tree.

Provides backwards-compatible response shape for the existing OA expense form
budget account picker (previously served by expense-api/api/v1/budget.py).
"""
from fastapi import APIRouter

from app.core.deps import CurrentUserPayload, SessionDep
from app.crud import catalog as catalog_crud
from app.schemas.hierarchy import HierarchyAccount, HierarchyL1, HierarchyResponse

router = APIRouter(tags=["hierarchy"])


@router.get("/hierarchy", response_model=HierarchyResponse)
async def get_hierarchy(db: SessionDep, user: CurrentUserPayload):  # noqa: ARG001
    """Returns active L1 + Account tree. Cost center mapping is handled client-side
    via cost_center_id selected separately on the OA form."""
    l1s = await catalog_crud.list_l1(db, is_active=True)
    groups: list[HierarchyL1] = []
    for l1 in l1s:
        groups.append(HierarchyL1(
            id=l1.id, code=l1.code, name=l1.name,
            accounts=[
                HierarchyAccount(id=a.id, code=a.code, name=a.name)
                for a in l1.accounts if a.is_active
            ],
        ))
    return HierarchyResponse(l1_groups=groups)
