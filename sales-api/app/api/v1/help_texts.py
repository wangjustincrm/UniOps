"""Help texts the front end shows next to what they explain (the user, 2026-11-16). Read-only;
sales.read to look. The texts live in the domain (app/domain/status_help.py), beside the rules."""
from typing import Annotated, Any

from fastapi import Depends

from app.core.deps import get_uow
from app.domain.status_help import payload
from app.platform.authorization import requires
from app.platform.routes import Router

UoW = Annotated[Any, Depends(get_uow)]

router = Router(prefix="/help", tags=["help"])


class HelpService:
    def __init__(self, uow: Any) -> None:
        self.uow = uow

    @requires("sales.read")
    async def texts(self) -> dict:
        return payload()


@router.get("/texts")
async def help_texts(u: UoW) -> dict:
    """Every help text: the order statuses, hold reasons and details, and the glossary's terms."""
    return await HelpService(u).texts()
