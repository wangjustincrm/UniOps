"""The equipment register, and loading it from a spreadsheet.

HSE asked for both: pick a machine and have its department fill itself in, and
"make the table easy to update and can automatically be populated based on an
excel sheet upload."

The upload matches on `asset_no` and updates rather than inserting a second row,
because the realistic use is re-uploading a corrected sheet, not a one-time
import. A row without an asset number is matched on name instead — imperfect,
but the alternative is silently duplicating equipment every time the file is
sent again.
"""
import csv
import io
import uuid

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from app.core.deps import SessionDep
from app.core.permissions import CanManageSettings, CanReportIncident
from app.crud.incident import _resolve_location_path
from app.models.equipment import Equipment
from app.models.mirrors import Department, Location

router = APIRouter(prefix="/equipment")


class EquipmentIn(BaseModel):
    asset_no: str | None = Field(default=None, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    equipment_type: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=150)
    model: str | None = Field(default=None, max_length=150)
    serial_no: str | None = Field(default=None, max_length=100)
    location_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    is_active: bool = True


class EquipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_no: str | None
    name: str
    equipment_type: str | None
    location_id: uuid.UUID | None
    location_path: str | None
    department_id: uuid.UUID | None
    department_name: str | None
    is_active: bool


class ImportResult(BaseModel):
    created: int
    updated: int
    skipped: int
    problems: list[str]


async def _fill_place(db: SessionDep, row: Equipment) -> None:
    """Denormalise location and department so a picker needs one query."""
    row.location_path = await _resolve_location_path(db, row.location_id)
    if row.department_id:
        row.department_name = (await db.execute(
            select(Department.name).where(Department.id == row.department_id))).scalar()
    else:
        row.department_name = None


@router.get("", response_model=list[EquipmentOut])
async def list_equipment(
    db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    search: str | None = None, location_id: uuid.UUID | None = None,
    include_inactive: bool = False, limit: int = Query(default=200, le=500),
):
    q = select(Equipment)
    if not include_inactive:
        q = q.where(Equipment.is_active.is_(True))
    if location_id:
        q = q.where(Equipment.location_id == location_id)
    if search:
        like = f"%{search}%"
        q = q.where(or_(Equipment.name.ilike(like), Equipment.asset_no.ilike(like)))
    return list((await db.execute(q.order_by(Equipment.name).limit(limit))).scalars().all())


@router.post("", response_model=EquipmentOut, status_code=201)
async def create_equipment(payload: EquipmentIn, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    row = Equipment(id=uuid.uuid4(), **payload.model_dump())
    await _fill_place(db, row)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


@router.patch("/{equipment_id}", response_model=EquipmentOut)
async def update_equipment(
    equipment_id: uuid.UUID, payload: EquipmentIn, db: SessionDep, user: CanManageSettings,  # noqa: ARG001
):
    row = await db.get(Equipment, equipment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipment not found")
    # ★ Only what was sent. The form sends four fields; writing the whole model
    # blanked manufacturer, model, serial, description and department on every
    # save, and put a retired machine back in service.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await _fill_place(db, row)
    await db.flush()
    await db.refresh(row)
    return row


@router.post("/import", response_model=ImportResult)
async def import_equipment(file: UploadFile, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Load or refresh the register from a CSV export of HSE's spreadsheet.

    CSV rather than xlsx: ehs-api has no spreadsheet library, and adding one to
    read a file somebody exports in two clicks is a dependency that has to be
    installed in every image forever. The columns are matched case-insensitively
    so a sheet with "Asset No" or "asset_no" both work.

    Areas and departments are matched by name, and a row naming one that does
    not exist is reported rather than dropped — a silently skipped row in an
    import of four hundred is invisible.
    """
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The file has no header row")

    cols = {(c or "").strip().lower().replace(" ", "_"): c for c in reader.fieldnames}

    def pick(row: dict, *names: str) -> str | None:
        for n in names:
            col = cols.get(n)
            if col and (row.get(col) or "").strip():
                return row[col].strip()
        return None

    locations = {
        (name or "").lower(): (lid, path)
        for lid, name, path in (await db.execute(
            select(Location.id, Location.name, Location.path))).all()
    }
    departments = {
        (name or "").lower(): did
        for did, name in (await db.execute(
            select(Department.id, Department.name))).all()
    }

    created = updated = skipped = 0
    problems: list[str] = []

    for i, csv_row in enumerate(reader, start=2):
        name = pick(csv_row, "name", "equipment", "equipment_name")
        if not name:
            skipped += 1
            continue

        asset_no = pick(csv_row, "asset_no", "asset", "tag", "asset_number")
        existing = None
        if asset_no:
            existing = (await db.execute(
                select(Equipment).where(Equipment.asset_no == asset_no))).scalar_one_or_none()
        if existing is None:
            existing = (await db.execute(
                select(Equipment).where(Equipment.name == name,
                                        Equipment.asset_no.is_(None)))).scalar_one_or_none()

        row = existing or Equipment(id=uuid.uuid4(), name=name)
        row.name = name
        row.asset_no = asset_no or row.asset_no
        row.equipment_type = pick(csv_row, "type", "equipment_type") or row.equipment_type
        row.manufacturer = pick(csv_row, "manufacturer", "make") or row.manufacturer
        row.model = pick(csv_row, "model") or row.model
        row.serial_no = pick(csv_row, "serial", "serial_no") or row.serial_no

        area = pick(csv_row, "area", "location", "plant_area")
        if area:
            hit = locations.get(area.lower())
            if hit:
                row.location_id, row.location_path = hit
            else:
                problems.append(f"Row {i}: no area called '{area}'")

        dept = pick(csv_row, "department", "dept")
        if dept:
            did = departments.get(dept.lower())
            if did:
                row.department_id, row.department_name = did, dept
            else:
                problems.append(f"Row {i}: no department called '{dept}'")

        if existing is None:
            db.add(row)
            created += 1
        else:
            updated += 1

    await db.flush()
    # Enough to act on, not so many that the response is unreadable.
    return ImportResult(created=created, updated=updated, skipped=skipped,
                        problems=problems[:50])
