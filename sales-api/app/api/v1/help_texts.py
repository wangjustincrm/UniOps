"""Help texts the front end shows next to what they explain (the user, 2026-11-16). Read-only;
sales.read to look. The texts live in the domain (app/domain/status_help.py), beside the rules."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.deps import get_uow
from app.domain.status_help import payload
from app.platform.authorization import requires

UoW = Annotated[Any, Depends(get_uow)]

router = APIRouter(prefix="/help", tags=["help"])


class HelpService:
    def __init__(self, uow: Any) -> None:
        self.uow = uow

    @requires("sales.read")
    async def order_statuses(self) -> dict:
        return payload()


@router.get("/order-statuses")
async def order_status_help(u: UoW) -> dict:
    return await HelpService(u).order_statuses()
