"""Attendee directory search endpoint (Task 11).

Provides server-side attendee lookup for the booking invite picker.
"""
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from app.core.deps import SessionDep
from app.core.permissions import CurrentUser
from app.models.user_mirror import User
from app.schemas.directory import DirectoryUserOut

router = APIRouter()


@router.get("/directory")
async def search_directory(
    db: SessionDep,
    _: CurrentUser,
    q: Annotated[str, Query()] = "",
) -> list[DirectoryUserOut]:
    """Search the local users mirror for active users (attendees).

    Query parameter `q` is matched against full_name and email (case-insensitive
    partial match). Users with empty email are excluded (cannot receive invites).
    Results are sorted by full_name and limited to 50.

    Args:
        db: Database session.
        _: CurrentUser permission check (gated by "view_booking" permission).
        q: Optional search query (empty → all active users with non-empty email).

    Returns:
        List of up to 50 users matching the query (or all if no query).
    """
    # Base conditions: active users with non-empty email
    base_conditions = [
        User.is_active == True,
        User.email.isnot(None),
        User.email != "",  # Exclude users with empty email
    ]

    if q:
        # If q provided, match against full_name OR email (case-insensitive)
        q_pattern = f"%{q}%"
        search_condition = or_(
            User.full_name.ilike(q_pattern),
            User.email.ilike(q_pattern),
        )
        base_conditions.append(search_condition)

    # Build and execute query
    stmt = (
        select(User)
        .where(*base_conditions)
        .order_by(User.full_name)
        .limit(50)
    )

    result = await db.execute(stmt)
    users = result.scalars().all()

    # Convert to response schema (from_attributes=True handles ORM objects)
    return [DirectoryUserOut.model_validate(u) for u in users]
