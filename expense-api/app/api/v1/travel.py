"""Travel-application read endpoints: eligible TRAs for the TRV picker, user directory."""
import logging
import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.deps import CurrentUserDep, SessionDep
from app.crud import expense as expense_crud

log = logging.getLogger(__name__)

router = APIRouter(tags=["travel"])


class EligibleTravelApp(BaseModel):
    id: uuid.UUID
    claim_number: str
    travel_destination: str | None = None
    travel_from_date: str | None = None
    travel_to_date: str | None = None
    purpose: str | None = None


@router.get("/travel-applications/eligible", response_model=list[EligibleTravelApp])
async def eligible_travel_apps(db: SessionDep, user: CurrentUserDep):
    user_id = uuid.UUID(user["sub"])
    rows = await expense_crud.list_eligible_travel_apps(db, user_id)
    return [EligibleTravelApp(
        id=c.id, claim_number=c.claim_number, travel_destination=c.travel_destination,
        travel_from_date=c.travel_from_date.isoformat() if c.travel_from_date else None,
        travel_to_date=c.travel_to_date.isoformat() if c.travel_to_date else None,
        purpose=c.purpose) for c in rows]


class DirectoryUser(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None = None


@router.get("/users/directory", response_model=list[DirectoryUser])
async def user_directory(db: SessionDep, _: CurrentUserDep, q: str = ""):
    """Search active users by name/email (shared users table). Limit 20.

    Best-effort like the actor-name lookup in app/api/v1/expenses.py: the
    `users` table is identity-owned and absent in this service's test DB, so a
    missing table (or any query error) returns `[]` instead of 500ing.
    """
    like = f"%{q.strip()}%"
    try:
        rows = (await db.execute(text(
            "SELECT id, full_name, email FROM users "
            "WHERE is_active = true AND (full_name ILIKE :like OR email ILIKE :like) "
            "ORDER BY full_name LIMIT 20"), {"like": like})).all()
    except DBAPIError:
        await db.rollback()
        log.warning("user_directory: `users` table query failed (missing table?)", exc_info=True)
        return []
    return [DirectoryUser(id=r[0], full_name=r[1] or "", email=r[2]) for r in rows]
