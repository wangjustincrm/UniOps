"""GET /api/v1/area-rules — what each access area requires.

Read by the VMS frontend so its hints ("needs Quality Manager approval", the
health-declaration and compliance sections) come from the same rules the
server enforces instead of a second hard-coded copy.
"""
from fastapi import APIRouter

from app.core.deps import CurrentUserPayload
from app.services import area_rules

router = APIRouter(prefix="/area-rules", tags=["area-rules"])


@router.get("", response_model=list[dict])
async def list_area_rules(_: CurrentUserPayload):
    return area_rules.all_rules()
