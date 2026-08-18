"""material_suppliers CRUD — supply parameters, hand-maintained (MRP phase0
task 6). Phase 1 purchase-suggestion logic picks the default supplier + lead
time for a material via `is_primary`.

Reads: any authenticated role (materials.py/boms.py idiom). Writes: gated on
require_any_permission("data_maintenance", "mdm.bom.write") — same rationale
as boms.py's POST /sync (see that module's docstring): this table is
material/supplier governance data, so it accepts the narrower `mdm.bom.write`
key too, without dropping the pre-existing `data_maintenance` admins.
"""
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_any_permission
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.material_supplier import MaterialSupplier

router = APIRouter(prefix="/material-suppliers", tags=["material-suppliers"])

WriteDep = Annotated[dict, Depends(require_any_permission("data_maintenance", "mdm.bom.write"))]


class MaterialSupplierCreate(BaseModel):
    material_code: str
    partner_code: str
    lead_time_days: int | None = None
    moq: Decimal | None = None
    order_multiple: Decimal | None = None
    is_primary: bool = False
    price_ref: Decimal | None = None
    notes: str | None = None


class MaterialSupplierUpdate(BaseModel):
    # Re-sourcing a material to a different supplier is an ordinary event, so
    # `partner_code` is editable. `material_code` is NOT: the row's other
    # values are quantities in THAT material's unit (1000 KGM of lactose says
    # nothing about a tin can counted in pieces), so moving a row to another
    # material would silently carry meaningless numbers across.
    partner_code: str | None = None
    lead_time_days: int | None = None
    moq: Decimal | None = None
    order_multiple: Decimal | None = None
    is_primary: bool | None = None
    price_ref: Decimal | None = None
    notes: str | None = None


class MaterialSupplierResponse(BaseModel):
    id: uuid.UUID
    material_code: str
    partner_code: str
    lead_time_days: int | None
    moq: Decimal | None
    order_multiple: Decimal | None
    is_primary: bool
    price_ref: Decimal | None
    notes: str | None

    model_config = {"from_attributes": True}


class MaterialSupplierListResponse(BaseModel):
    items: list[MaterialSupplierResponse]
    total: int
    page: int
    page_size: int


def _conflict_detail(material_code: str, partner_code: str) -> str:
    return (
        f"material_suppliers row for material_code={material_code!r} "
        f"partner_code={partner_code!r} already exists"
    )


@router.get("", response_model=MaterialSupplierListResponse)
async def list_material_suppliers(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    material_code: str | None = Query(default=None),
    partner_code: str | None = Query(default=None),
    is_primary: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    query = select(MaterialSupplier)
    if material_code:
        query = query.where(MaterialSupplier.material_code == material_code)
    if partner_code:
        query = query.where(MaterialSupplier.partner_code == partner_code)
    if is_primary is not None:
        query = query.where(MaterialSupplier.is_primary.is_(is_primary))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = list((await db.execute(
        query.order_by(MaterialSupplier.material_code, MaterialSupplier.partner_code)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return MaterialSupplierListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=MaterialSupplierResponse, status_code=status.HTTP_201_CREATED)
async def create_material_supplier(
    body: MaterialSupplierCreate,
    _: WriteDep,
    db: AsyncSession = Depends(get_db),
):
    row = MaterialSupplier(**body.model_dump())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_detail(body.material_code, body.partner_code),
        )
    await db.commit()
    return row


class MaterialSupplierBulkRow(BaseModel):
    """One pasted row. Everything but the natural key is optional: a planner
    filling in lead times should not have to restate an MOQ they are not
    changing."""
    material_code: str
    partner_code: str
    lead_time_days: int | None = None
    moq: Decimal | None = None
    order_multiple: Decimal | None = None
    is_primary: bool = False
    price_ref: Decimal | None = None
    notes: str | None = None


class MaterialSupplierBulkBody(BaseModel):
    rows: list[MaterialSupplierBulkRow]


class MaterialSupplierBulkError(BaseModel):
    """1-based row number as the planner sees it in the paste box, plus a
    sentence they can act on."""
    row: int
    material_code: str
    partner_code: str
    message: str


class MaterialSupplierBulkResult(BaseModel):
    created: int
    updated: int
    errors: list[MaterialSupplierBulkError]


@router.post("/bulk", response_model=MaterialSupplierBulkResult)
async def bulk_upsert_material_suppliers(
    body: MaterialSupplierBulkBody,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = None,
):
    """Upsert supply parameters by their natural key, row by row.

    Supply parameters arrive as a spreadsheet from purchasing. Posting them
    one at a time means hundreds of round trips and, worse, a half-applied
    paste when one row is wrong. Here each row is committed on its own and a
    bad row is REPORTED rather than rolling the batch back: the other rows
    are correct, the planner typed them, and making them retype everything
    to fix one typo is how people go back to keeping the data in Excel.

    Declared before `/{row_id}` so the literal path is not parsed as a UUID.
    """
    created = updated = 0
    errors: list[MaterialSupplierBulkError] = []

    for index, row in enumerate(body.rows, start=1):
        material_code = (row.material_code or "").strip()
        partner_code = (row.partner_code or "").strip()

        def _fail(message: str) -> None:
            errors.append(MaterialSupplierBulkError(
                row=index, material_code=material_code,
                partner_code=partner_code, message=message,
            ))

        if not material_code or not partner_code:
            _fail("material code and supplier code are both required")
            continue
        if row.lead_time_days is not None and row.lead_time_days < 0:
            _fail("lead time cannot be negative")
            continue
        if row.moq is not None and row.moq < 0:
            _fail("minimum order quantity cannot be negative")
            continue
        if row.order_multiple is not None and row.order_multiple <= 0:
            _fail("order multiple must be greater than 0")
            continue

        existing = (await db.execute(
            select(MaterialSupplier).where(
                MaterialSupplier.material_code == material_code,
                MaterialSupplier.partner_code == partner_code,
            )
        )).scalars().first()

        values = row.model_dump(exclude={"material_code", "partner_code"})
        try:
            if existing is None:
                db.add(MaterialSupplier(
                    material_code=material_code, partner_code=partner_code, **values))
                await db.commit()
                created += 1
            else:
                # Only what the row actually carries: a blank cell means
                # "leave it alone", not "clear it". `is_primary` is the
                # exception -- it is a checkbox with no blank state, so a
                # False in the paste really does mean not primary.
                for field, value in values.items():
                    if value is not None or field == "is_primary":
                        setattr(existing, field, value)
                await db.commit()
                updated += 1
        except IntegrityError:
            await db.rollback()
            # The partial unique index allows one primary supplier per
            # material. Hitting it is a data question only a human can
            # settle, so it comes back as a readable row error rather than
            # a 500 that loses the whole paste.
            _fail(
                f"{material_code} already has a different primary supplier; "
                f"clear that one first or paste this row without the primary flag"
            )

    return MaterialSupplierBulkResult(created=created, updated=updated, errors=errors)


@router.patch("/{row_id}", response_model=MaterialSupplierResponse)
async def update_material_supplier(
    row_id: uuid.UUID,
    body: MaterialSupplierUpdate,
    _: WriteDep,
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(MaterialSupplier).where(MaterialSupplier.id == row_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="material_suppliers row not found")
    updates = body.model_dump(exclude_unset=True)
    if "partner_code" in updates and not (updates["partner_code"] or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="supplier code cannot be blank",
        )
    for k, v in updates.items():
        setattr(row, k, v.strip() if k == "partner_code" and isinstance(v, str) else v)
    # Capture BEFORE flush: after a flush raises, the session's transaction
    # is left in a state that requires rollback before any further ORM
    # attribute access — reading `row.material_code`/`row.partner_code` in
    # the `except` branch below would trigger an implicit re-SELECT (an
    # expired attribute load) against that dead transaction and raise
    # PendingRollbackError instead of cleanly returning 409. This path was
    # previously unreachable (the old, unfiltered material_code+partner_code
    # unique constraint could never be hit by a PATCH, which didn't touch
    # either field) — the partial `is_primary` index (migration 0013) made it
    # reachable, and a PATCH that re-sources the row to another supplier can
    # now collide with the material+partner pair itself. Both land here.
    #
    # The codes are read AFTER the assignments above on purpose: the pair
    # reported back is the one that actually collided, not the one the row
    # used to hold.
    material_code, partner_code = row.material_code, row.partner_code
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_detail(material_code, partner_code),
        )
    await db.commit()
    return row


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material_supplier(
    row_id: uuid.UUID,
    _: WriteDep,
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(MaterialSupplier).where(MaterialSupplier.id == row_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="material_suppliers row not found")
    await db.delete(row)
    await db.commit()
