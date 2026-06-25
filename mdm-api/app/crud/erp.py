from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.erp_material import ErpMaterial
from app.models.erp_supplier import ErpSupplier
from app.models.erp_person import ErpPerson


async def list_materials(
    db: AsyncSession, *, search: str | None = None, part_status: str | None = None,
    item_mes_type: str | None = None, page: int = 1, page_size: int = 20,
) -> tuple[list[ErpMaterial], int]:
    q = select(ErpMaterial)
    if search:
        term = f"%{search}%"
        q = q.where(ErpMaterial.erp_part_no.ilike(term) | ErpMaterial.description.ilike(term))
    if part_status:
        q = q.where(ErpMaterial.part_status == part_status)
    if item_mes_type:
        q = q.where(ErpMaterial.item_mes_type == item_mes_type)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(
        q.order_by(ErpMaterial.erp_part_no).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def get_material_by_code(db: AsyncSession, code: str) -> ErpMaterial | None:
    return (await db.execute(
        select(ErpMaterial).where(ErpMaterial.erp_part_no == code)
    )).scalar_one_or_none()


async def list_suppliers(
    db: AsyncSession, *, search: str | None = None, exclude_codes: list[str] | None = None,
    page: int = 1, page_size: int = 20,
) -> tuple[list[ErpSupplier], int]:
    q = select(ErpSupplier)
    if search:
        term = f"%{search}%"
        q = q.where(
            ErpSupplier.erp_supplier_code.ilike(term) | ErpSupplier.supplier_name.ilike(term)
        )
    if exclude_codes:
        q = q.where(~ErpSupplier.erp_supplier_code.in_(exclude_codes))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(
        q.order_by(ErpSupplier.supplier_name).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def get_supplier_by_code(db: AsyncSession, code: str) -> ErpSupplier | None:
    return (await db.execute(
        select(ErpSupplier).where(ErpSupplier.erp_supplier_code == code)
    )).scalar_one_or_none()


async def list_persons(
    db: AsyncSession, *, search: str | None = None, exclude_codes: list[str] | None = None,
    page: int = 1, page_size: int = 20,
) -> tuple[list[ErpPerson], int]:
    q = select(ErpPerson)
    if search:
        term = f"%{search}%"
        q = q.where(
            ErpPerson.erp_person_code.ilike(term) | ErpPerson.person_name.ilike(term)
        )
    if exclude_codes:
        q = q.where(~ErpPerson.erp_person_code.in_(exclude_codes))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(
        q.order_by(ErpPerson.person_name).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def get_person_by_code(db: AsyncSession, code: str) -> ErpPerson | None:
    return (await db.execute(
        select(ErpPerson).where(ErpPerson.erp_person_code == code)
    )).scalar_one_or_none()
