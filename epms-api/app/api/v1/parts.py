"""Parts Catalog endpoints."""
import csv
import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.deps import CurrentUserPayload, SessionDep, require_permission, require_roles
from app.crud import part as part_crud
from app.schemas.part import (
    PartCreate,
    PartCsvRow,
    PartImportResult,
    PartListResponse,
    PartResponse,
    PartUpdate,
)

router = APIRouter(prefix="/parts", tags=["parts"])

AdminDep = Annotated[dict, Depends(require_permission("parts_catalog"))]

_CSV_FIELDS = [
    "code", "category", "name", "description",
    "supplier", "supplier_part_no", "supplier_item_id",
    "unit_price", "unit",
]


# ── List / Get ─────────────────────────────────────────────────────────────────

@router.get("", response_model=PartListResponse)
async def list_parts(
    db: SessionDep,
    _: CurrentUserPayload,
    category: str | None = Query(default=None),
    supplier: str | None = Query(default=None),
    search: str | None = Query(default=None),
    active_only: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=500),
):
    items, total = await part_crud.get_all(
        db,
        category=category,
        supplier=supplier,
        search=search,
        active_only=active_only,
        page=page,
        page_size=page_size,
    )
    return PartListResponse(items=items, total=total)


@router.get("/categories", response_model=list[str])
async def list_categories(db: SessionDep, _: CurrentUserPayload):
    return await part_crud.get_categories(db)


@router.get("/export", response_class=StreamingResponse)
async def export_parts(db: SessionDep, _: CurrentUserPayload):
    """Download all parts as CSV."""
    parts = await part_crud.get_all_for_export(db)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for p in parts:
        writer.writerow({
            "code": p.code,
            "category": p.category,
            "name": p.name,
            "description": p.description or "",
            "supplier": p.supplier,
            "supplier_part_no": p.supplier_part_no,
            "supplier_item_id": p.supplier_item_id or "",
            "unit_price": str(p.unit_price),
            "unit": p.unit,
        })
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=parts.csv"},
    )


@router.get("/{part_id}", response_model=PartResponse)
async def get_part(part_id: uuid.UUID, db: SessionDep, _: CurrentUserPayload):
    part = await part_crud.get_by_id(db, part_id)
    if part is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Part not found")
    return part


# ── Create / Update / Delete ───────────────────────────────────────────────────

@router.post("", response_model=PartResponse, status_code=status.HTTP_201_CREATED)
async def create_part(body: PartCreate, db: SessionDep, _: AdminDep):
    if await part_crud.get_by_code(db, body.code):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Part code already exists")
    return await part_crud.create(db, body)


@router.patch("/{part_id}", response_model=PartResponse)
async def update_part(part_id: uuid.UUID, body: PartUpdate, db: SessionDep, _: AdminDep):
    part = await part_crud.get_by_id(db, part_id)
    if part is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Part not found")
    return await part_crud.update(db, part, body)


@router.delete("/{part_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_part(part_id: uuid.UUID, db: SessionDep, _: AdminDep):
    part = await part_crud.get_by_id(db, part_id)
    if part is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Part not found")
    await part_crud.delete(db, part)


# ── CSV import ─────────────────────────────────────────────────────────────────

@router.post("/import", response_model=PartImportResult)
async def import_parts(file: UploadFile, db: SessionDep, _: AdminDep):
    """Upsert parts from a CSV file (BOM-safe UTF-8)."""
    raw = await file.read()
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[PartCsvRow] = []
    parse_errors: list[str] = []

    for i, row in enumerate(reader, start=2):
        try:
            rows.append(PartCsvRow(
                code=row.get("code", "").strip(),
                category=row.get("category", "").strip(),
                name=row.get("name", "").strip(),
                description=row.get("description", "").strip() or None,
                supplier=row.get("supplier", "").strip(),
                supplier_part_no=row.get("supplier_part_no", "").strip(),
                supplier_item_id=row.get("supplier_item_id", "").strip() or None,
                unit_price=row.get("unit_price", "0").strip() or "0",
                unit=row.get("unit", "").strip(),
            ))
        except Exception as exc:
            parse_errors.append(f"Row {i}: {exc}")

    result = await part_crud.import_from_csv(db, rows)
    result.errors = parse_errors + result.errors
    return result
