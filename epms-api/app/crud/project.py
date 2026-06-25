"""CRUD operations for Project."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.schemas.project import ProjectCreate, ProjectUpdate


async def get_all(
    db: AsyncSession,
    *,
    status: str | None = None,
    search: str | None = None,
) -> list[Project]:
    q = select(Project)
    if status:
        q = q.where(Project.status == status)
    if search:
        term = f"%{search}%"
        q = q.where(Project.name.ilike(term) | Project.code.ilike(term))
    result = await db.execute(q.order_by(Project.code))
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, project_id: uuid.UUID) -> Project | None:
    result = await db.execute(select(Project).where(Project.id == project_id))
    return result.scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> Project | None:
    result = await db.execute(select(Project).where(Project.code == code.upper()))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, payload: ProjectCreate) -> Project:
    project = Project(
        code=payload.code.upper(),
        name=payload.name,
        description=payload.description,
        status=payload.status,
        budget=payload.budget,
        currency=payload.currency,
        start_date=payload.start_date,
        end_date=payload.end_date,
        owner_dept=payload.owner_dept,
        manager_name=payload.manager_name,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project


async def update(db: AsyncSession, project: Project, payload: ProjectUpdate) -> Project:
    for field in (
        "name", "description", "status", "budget", "currency",
        "start_date", "end_date", "owner_dept", "manager_name",
    ):
        val = getattr(payload, field)
        if val is not None:
            setattr(project, field, val)
    await db.flush()
    await db.refresh(project)
    return project
