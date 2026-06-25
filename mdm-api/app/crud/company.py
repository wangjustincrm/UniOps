import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.company import Company
from app.schemas.company import CompanyCreate, CompanyUpdate


async def get_all(db: AsyncSession, *, active_only: bool = False) -> list[Company]:
    q = select(Company)
    if active_only:
        q = q.where(Company.is_active.is_(True))
    return list((await db.execute(q.order_by(Company.code))).scalars().all())


async def get_by_id(db: AsyncSession, company_id: uuid.UUID) -> Company | None:
    return (await db.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> Company | None:
    return (await db.execute(select(Company).where(Company.code == code.upper()))).scalar_one_or_none()


async def create(db: AsyncSession, payload: CompanyCreate) -> Company:
    company = Company(**payload.model_dump())
    company.code = company.code.upper()
    db.add(company)
    await db.flush()
    await db.refresh(company)
    return company


async def update(db: AsyncSession, company: Company, payload: CompanyUpdate) -> Company:
    for field, val in payload.model_dump(exclude_none=True).items():
        setattr(company, field, val)
    await db.flush()
    await db.refresh(company)
    return company
