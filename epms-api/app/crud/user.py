"""CRUD operations for the User model."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.schemas.auth import RegisterRequest
from app.schemas.user import UserCreate


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, payload: RegisterRequest) -> User:
    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        department_id=payload.department_id,
        password_changed_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()   # populate id without committing — session.py commits on exit
    await db.refresh(user)
    return user


async def create_admin(db: AsyncSession, payload: UserCreate) -> User:
    """Create a user via admin panel (accepts explicit password, full role set)."""
    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        department_id=payload.department_id,
        supervisor_id=payload.supervisor_id,
        is_active=payload.is_active,
        teams_account=payload.teams_account,
        notification_channel=payload.notification_channel,
        must_change_password=payload.must_change_password,
        erp_person_code=payload.erp_person_code,
        password_changed_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def delete(db: AsyncSession, user: User) -> None:
    await db.delete(user)
    await db.flush()


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    """Return the user if email+password are valid and account is active, else None."""
    user = await get_by_email(db, email)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def update_me(
    db: AsyncSession,
    user: User,
    full_name: str | None,
    teams_account: str | None,
    notification_channel: str | None = None,
) -> User:
    if full_name is not None:
        user.full_name = full_name
    if teams_account is not None:
        user.teams_account = teams_account
    if notification_channel is not None:
        user.notification_channel = notification_channel
    await db.flush()
    await db.refresh(user)
    return user


async def enable_mfa(db: AsyncSession, user: User) -> None:
    user.mfa_enabled = True
    await db.flush()


async def disable_mfa(db: AsyncSession, user: User) -> None:
    user.mfa_enabled = False
    await db.flush()


async def get_by_erp_person_code(db, code: str):
    return (await db.execute(select(User).where(User.erp_person_code == code))).scalar_one_or_none()
