"""Dashboard aggregation endpoints."""
import uuid

from fastapi import APIRouter

from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud import dashboard as dash_crud
from app.schemas.dashboard import DashboardResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
async def get_dashboard(db: SessionDep, user: CurrentUserPayload, token: BearerToken):
    """
    Return role-appropriate dashboard KPIs and data sections.
    The response shape varies by role — sections irrelevant to the
    caller's role are omitted (null).
    """
    role = user.get("role", "requester")
    user_id = uuid.UUID(user["sub"])
    # user + token: a Department Manager's figures are scoped to what they can
    # see, and the Budget Overview is read from budget-api as the caller.
    return await dash_crud.build(db, role, user_id, user=user, token=token)
