"""Budget settings (single-row config)."""
import uuid

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUserPayload, SessionDep, require_roles
from app.crud import settings as settings_crud
from app.schemas.settings import BudgetSettingsResponse, BudgetSettingsUpdate

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=BudgetSettingsResponse)
async def get_settings(db: SessionDep, user: CurrentUserPayload):  # noqa: ARG001
    row = await settings_crud.get_settings(db)
    return BudgetSettingsResponse.model_validate(row)


@router.patch("/settings", response_model=BudgetSettingsResponse)
async def update_settings(
    payload: BudgetSettingsUpdate, db: SessionDep,
    user: dict = Depends(require_roles("system_admin")),
):
    actor_id = uuid.UUID(user["sub"])
    row = await settings_crud.update_settings(db, payload, actor_id)
    return BudgetSettingsResponse.model_validate(row)
