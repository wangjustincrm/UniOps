import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User


async def get_all(
    db: AsyncSession,
    *,
    role: str | None = None,
    department_id: uuid.UUID | None = None,
    active_only: bool = False,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[User], int]:
    q = select(User)
    if role:
        q = q.where(User.role == role)
    if department_id:
        q = q.where(User.department_id == department_id)
    if active_only:
        q = q.where(User.is_active.is_(True))
    if search:
        term = f"%{search}%"
        q = q.where(User.full_name.ilike(term) | User.email.ilike(term))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(q.order_by(User.full_name).offset((page - 1) * page_size).limit(page_size))).scalars().all())
    return items, total


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
