"""GET /api/v1/area-rules — every access zone and what it requires.

Read by the VMS frontend for the zone picker, the zone labels, and its hints
("needs approval by …", the health-declaration and compliance sections), so
they come from the same rules the server enforces. Retired zones are included
(`is_active: false`) so old visits still show their zone's name; the picker
offers active ones only.
"""
from fastapi import APIRouter
from sqlalchemy import select

from app.core.deps import CurrentUserPayload, SessionDep
from app.models.user_mirror import User
from app.services import area_rules

router = APIRouter(prefix="/area-rules", tags=["area-rules"])


@router.get("", response_model=list[dict])
async def list_area_rules(db: SessionDep, _: CurrentUserPayload):
    rules = await area_rules.all_rules(db)
    ids = {u for r in rules for u in r.manager_ids}
    names = {}
    if ids:
        names = dict((await db.execute(
            select(User.id, User.full_name).where(User.id.in_(ids))
        )).all())
    return [r.as_dict(names) for r in rules]
