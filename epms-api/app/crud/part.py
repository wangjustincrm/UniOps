"""CRUD operations for Parts Catalog."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.part import Part
from app.schemas.part import PartCreate, PartCsvRow, PartImportResult, PartUpdate


def _build_query(
    category: str | None,
    supplier: str | None,
    search: str | None,
    active_only: bool,
):
    q = select(Part)
    if category:
        q = q.where(Part.category == category)
    if supplier:
        q = q.where(Part.supplier == supplier)
    if search:
        term = f"%{search}%"
        q = q.where(
            Part.name.ilike(term) | Part.code.ilike(term) |
            Part.supplier_part_no.ilike(term) | Part.category.ilike(term) |
            Part.supplier.ilike(term)
        )
    if active_only:
        q = q.where(Part.is_active.is_(True))
    return q


async def get_all(
    db: AsyncSession,
    *,
    category: str | None = None,
    supplier: str | None = None,
    search: str | None = None,
    active_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Part], int]:
    base = _build_query(category, supplier, search, active_only)
    total: int = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    offset = (page - 1) * page_size
    items = list((await db.execute(base.order_by(Part.code).offset(offset).limit(page_size))).scalars().all())
    return items, total


async def get_all_for_export(
    db: AsyncSession,
) -> list[Part]:
    result = await db.execute(select(Part).order_by(Part.code))
    return list(result.scalars().all())


async def get_categories(db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(Part.category).where(Part.category != "").distinct().order_by(Part.category)
    )
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, part_id: uuid.UUID) -> Part | None:
    result = await db.execute(select(Part).where(Part.id == part_id))
    return result.scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> Part | None:
    result = await db.execute(select(Part).where(Part.code == code.upper()))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, payload: PartCreate) -> Part:
    part = Part(
        code=payload.code.upper(),
        category=payload.category,
        name=payload.name,
        description=payload.description,
        supplier=payload.supplier,
        supplier_part_no=payload.supplier_part_no,
        supplier_item_id=payload.supplier_item_id,
        unit_price=payload.unit_price,
        unit=payload.unit,
        image_data_url=payload.image_data_url,
    )
    db.add(part)
    await db.flush()
    await db.refresh(part)
    return part


async def update(db: AsyncSession, part: Part, payload: PartUpdate) -> Part:
    for field in (
        "category", "name", "description", "supplier", "supplier_part_no",
        "supplier_item_id", "unit_price", "unit", "image_data_url", "is_active",
    ):
        val = getattr(payload, field)
        if val is not None:
            setattr(part, field, val)
    await db.flush()
    await db.refresh(part)
    return part


async def delete(db: AsyncSession, part: Part) -> None:
    await db.delete(part)
    await db.flush()


async def import_from_csv(db: AsyncSession, rows: list[PartCsvRow]) -> PartImportResult:
    """Upsert parts by code."""
    # Cache existing parts by code (fetch all without pagination)
    all_parts = await get_all_for_export(db)
    part_map: dict[str, Part] = {p.code.upper(): p for p in all_parts}

    created = updated = 0
    errors: list[str] = []

    for row in rows:
        code = row.code.strip().upper()
        if not code:
            errors.append("Row skipped: empty code")
            continue

        part = part_map.get(code)
        if part is None:
            part = Part(
                code=code,
                category=row.category,
                name=row.name,
                description=row.description,
                supplier=row.supplier,
                supplier_part_no=row.supplier_part_no,
                supplier_item_id=row.supplier_item_id,
                unit_price=row.unit_price,
                unit=row.unit,
            )
            db.add(part)
            part_map[code] = part  # prevent duplicate within same CSV
            created += 1
        else:
            part.category = row.category
            part.name = row.name
            part.description = row.description
            part.supplier = row.supplier
            part.supplier_part_no = row.supplier_part_no
            part.supplier_item_id = row.supplier_item_id
            part.unit_price = row.unit_price
            part.unit = row.unit
            updated += 1

    await db.flush()
    return PartImportResult(created=created, updated=updated, errors=errors)
