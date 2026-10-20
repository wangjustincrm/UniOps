from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.erp_material import ErpMaterial
from app.models.erp_supplier import ErpSupplier
from app.models.erp_person import ErpPerson

# 现网 erp_materials.part_status 存的是 NC 的 ENABLESTATE 原值('2' 启用 / '3' 停用),
# 而前端(PR Type 1 选料器、Admin 的 Materials tab)一直按 'A'/'B' 过滤 —— 等值比较
# 永远不命中,两处都是空列表。这里让两种口径都能命中,所以将来写端归一成 'A'/'B'
# 之后这段不用再改,两种口径并存的窗口期也不会失效。
_STATUS_ALIASES = {"A": ("A", "2"), "B": ("B", "3")}


class UnknownSortKey(Exception):
    """?sort= named a column the list does not offer -> HTTP 422."""


# ?sort= keys -> ORDER BY expression. These dicts are the SQL-injection boundary;
# each key is a column the Portal / EPMS table shows. Text sorts case-insensitively.
MATERIAL_SORTABLE: dict[str, ColumnElement] = {
    "erp_part_no": func.lower(ErpMaterial.erp_part_no),
    "description": func.lower(ErpMaterial.description),
    "unit_meas": func.lower(ErpMaterial.unit_meas),
    "dim_quality": func.lower(ErpMaterial.dim_quality),
    "item_mes_type": func.lower(ErpMaterial.item_mes_type),
    "part_status": func.lower(ErpMaterial.part_status),
    "erp_rowversion": ErpMaterial.erp_rowversion,
}
SUPPLIER_SORTABLE: dict[str, ColumnElement] = {
    "erp_supplier_code": func.lower(ErpSupplier.erp_supplier_code),
    "supplier_name": func.lower(ErpSupplier.supplier_name),
    "supplier_type": func.lower(ErpSupplier.supplier_type),
    "supplier_tel": func.lower(ErpSupplier.supplier_tel),
    "supplier_address": func.lower(ErpSupplier.supplier_address),
}
PERSON_SORTABLE: dict[str, ColumnElement] = {
    "erp_person_code": func.lower(ErpPerson.erp_person_code),
    "person_name": func.lower(ErpPerson.person_name),
    "company_name": func.lower(ErpPerson.company_name),
    # The cell reads "<department_name> (<department_code>)".
    "department_name": func.lower(ErpPerson.department_name),
    # Shown as Yes/No: ascending puts "No" (false) first.
    "is_valid": ErpPerson.is_valid,
}


def _order(q, sort: str | None, dir: str, whitelist: dict, default, tiebreak):
    """No sort -> the list's historical order; else the whitelisted column,
    NULLs last in both directions, `tiebreak` keeping pages stable."""
    if sort is None:
        return q.order_by(default)
    if sort not in whitelist:
        raise UnknownSortKey(f"unknown sort column {sort!r}")
    expr = whitelist[sort]
    expr = expr.asc() if dir == "asc" else expr.desc()
    return q.order_by(expr.nulls_last(), tiebreak)


async def list_materials(
    db: AsyncSession, *, search: str | None = None, part_status: str | None = None,
    item_mes_type: str | None = None, page: int = 1, page_size: int = 20,
    sort: str | None = None, dir: str = "asc",
) -> tuple[list[ErpMaterial], int]:
    q = select(ErpMaterial)
    if search:
        term = f"%{search}%"
        q = q.where(ErpMaterial.erp_part_no.ilike(term) | ErpMaterial.description.ilike(term))
    if part_status:
        q = q.where(ErpMaterial.part_status.in_(
            _STATUS_ALIASES.get(part_status, (part_status,))))
    if item_mes_type:
        q = q.where(ErpMaterial.item_mes_type == item_mes_type)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(
        _order(q, sort, dir, MATERIAL_SORTABLE, ErpMaterial.erp_part_no, ErpMaterial.id)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def get_material_by_code(db: AsyncSession, code: str) -> ErpMaterial | None:
    return (await db.execute(
        select(ErpMaterial).where(ErpMaterial.erp_part_no == code)
    )).scalar_one_or_none()


async def list_suppliers(
    db: AsyncSession, *, search: str | None = None, exclude_codes: list[str] | None = None,
    page: int = 1, page_size: int = 20,
    sort: str | None = None, dir: str = "asc",
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
        _order(q, sort, dir, SUPPLIER_SORTABLE, ErpSupplier.erp_supplier_code, ErpSupplier.id)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def get_supplier_by_code(db: AsyncSession, code: str) -> ErpSupplier | None:
    return (await db.execute(
        select(ErpSupplier).where(ErpSupplier.erp_supplier_code == code)
    )).scalar_one_or_none()


async def list_persons(
    db: AsyncSession, *, search: str | None = None, exclude_codes: list[str] | None = None,
    page: int = 1, page_size: int = 20,
    sort: str | None = None, dir: str = "asc",
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
        _order(q, sort, dir, PERSON_SORTABLE, ErpPerson.person_name, ErpPerson.id)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def get_person_by_code(db: AsyncSession, code: str) -> ErpPerson | None:
    return (await db.execute(
        select(ErpPerson).where(ErpPerson.erp_person_code == code)
    )).scalar_one_or_none()
