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
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, case, func, or_, select, text

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanManageSettings, CanReportIncident
from app.crud.incident import _resolve_location_path
from app.models.equipment import Equipment
from app.models.mirrors import Department, Location, User
from app.models.vocabulary import VocabularyItem

from app.crud._list_order import lower_text, sort_clauses  # noqa: E402

router = APIRouter(prefix="/equipment")

SYSTEMS = "equipment_system"
PAGE_MAX = 200


class EquipmentIn(BaseModel):
    """A machine, added or edited by hand. On edit only what is sent changes."""

    asset_no: str | None = Field(default=None, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    system_id: uuid.UUID | None = None
    manufacturer: str | None = Field(default=None, max_length=150)
    model: str | None = Field(default=None, max_length=150)
    serial_no: str | None = Field(default=None, max_length=100)
    location_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None


class EquipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_no: str | None
    name: str
    description: str | None = None
    system_id: uuid.UUID | None = None
    system_label: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_no: str | None = None
    location_id: uuid.UUID | None
    location_path: str | None
    department_id: uuid.UUID | None
    department_name: str | None
    is_active: bool
    retired_at: datetime | None = None
    retired_by_name: str | None = None


class EquipmentPage(BaseModel):
    items: list[EquipmentOut]
    total: int
    # When the list was narrowed to an area: the area it was narrowed to. Not
    # always the one asked for — see `near_location_id`.
    scope_path: str | None = None


class SystemCount(BaseModel):
    system_id: uuid.UUID | None
    label: str
    count: int
    is_active: bool = True


class EquipmentSummary(BaseModel):
    total: int
    retired: int
    systems: list[SystemCount]


class ImportResult(BaseModel):
    created: int
    updated: int
    skipped: int
    problems: list[str]
    new_systems: list[str] = []


async def _fill_place(db: SessionDep, row: Equipment) -> None:
    """Denormalise location and department so a picker needs one query."""
    row.location_path = await _resolve_location_path(db, row.location_id)
    if row.department_id:
        row.department_name = (await db.execute(
            select(Department.name).where(Department.id == row.department_id))).scalar()
    else:
        row.department_name = None


async def _system(db: SessionDep, system_id: uuid.UUID | None) -> VocabularyItem | None:
    if system_id is None:
        return None
    item = await db.get(VocabularyItem, system_id)
    if item is None or item.vocabulary_code != SYSTEMS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No such System")
    return item


async def _out(db: SessionDep, rows: list[Equipment]) -> list[EquipmentOut]:
    """Rows with their System's *current* label (see the model)."""
    ids = {r.system_id for r in rows if r.system_id}
    labels = dict((await db.execute(
        select(VocabularyItem.id, VocabularyItem.label).where(VocabularyItem.id.in_(ids))
    )).all()) if ids else {}
    return [EquipmentOut.model_validate(r).model_copy(
        update={"system_label": labels.get(r.system_id)}) for r in rows]


def _in_subtree(path: str):
    """Machines placed at this area or anywhere beneath it."""
    below = select(Location.id).where(or_(Location.path == path,
                                          Location.path.like(path.replace("%", r"\%")
                                                             .replace("_", r"\_") + "/%")))
    return Equipment.location_id.in_(below)


async def _scope_for(db: SessionDep, location_id: uuid.UUID) -> str | None:
    """The area a picker should offer machines from, for a record filed at this one.

    Round 12, INC-10/INC-11: "when the 'where did this happen' location is set
    to the WasteWater Treatment Plant (WWTP), only the equipment … that have a
    location of wastewater treatment should be the choices available."

    ★ The area itself and everything under it — and when nothing is registered
    there (46 of the plant's 68 areas hold no machine; a room under a line
    usually has none of its own), the nearest area above it that has some.
    Filing in "Line 1 Hipex Room" then offers the Dryer's machines rather than
    an empty list. Returns None when no area on the way up has any.
    """
    path = (await db.execute(select(Location.path).where(Location.id == location_id))).scalar()
    if not path:
        return None
    parts = path.split("/")
    for depth in range(len(parts), 0, -1):
        candidate = "/".join(parts[:depth])
        hit = (await db.execute(select(Equipment.id).where(
            Equipment.is_active.is_(True), _in_subtree(candidate)).limit(1))).first()
        if hit:
            return candidate
    return None


# ★ The list standard (10-29, applied to Safety on 11-10): every column the
# register shows sorts on the server, since it is paged.
EQUIPMENT_SORTABLE = {
    "asset_no": lower_text(Equipment.asset_no),
    "name": lower_text(Equipment.name),
    "system": select(func.lower(VocabularyItem.label)).where(
        VocabularyItem.id == Equipment.system_id).correlate(Equipment).scalar_subquery(),
    "area": lower_text(Equipment.location_path),
    "department": lower_text(Equipment.department_name),
    # By the words shown, In service before Retired (re-review N6).
    "status": case((Equipment.is_active.is_(True), "In service"), else_="Retired"),
}


@router.get("", response_model=EquipmentPage)
async def list_equipment(
    db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    search: str | None = None,
    system_id: uuid.UUID | None = None,
    no_system: bool = Query(default=False, description="Only machines with no System"),
    near_location_id: uuid.UUID | None = Query(
        default=None, description="Narrow to the machines a record filed here could name"),
    state: str = Query(default="active", pattern="^(active|retired|all)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=PAGE_MAX),
    sort: str | None = Query(default=None, description="A key of EQUIPMENT_SORTABLE; unknown -> 422"),
    dir: str = Query(default="asc", pattern="^(asc|desc)$"),
):
    """A page of the register, with the total it is a page of.

    ★ Round 12, EQ-01: "the equipment registry does not show all the equipment"
    — it returned the first 200 of 7,170 by name and said nothing. A page now
    always carries the total, and every filter happens here, not in the browser.
    """
    conds = []
    if state == "active":
        conds.append(Equipment.is_active.is_(True))
    elif state == "retired":
        conds.append(Equipment.is_active.is_(False))
    if system_id:
        conds.append(Equipment.system_id == system_id)
    elif no_system:
        conds.append(Equipment.system_id.is_(None))
    scope = None
    if near_location_id:
        scope = await _scope_for(db, near_location_id)
        # Machines with no area at all are offered everywhere: nothing says
        # where they are, so nothing says they are not here.
        conds.append(or_(_in_subtree(scope), Equipment.location_id.is_(None))
                     if scope else Equipment.location_id.is_(None))
    if search and search.strip():
        like = f"%{search.strip()}%"
        sys_match = select(VocabularyItem.id).where(
            VocabularyItem.vocabulary_code == SYSTEMS, VocabularyItem.label.ilike(like))
        conds.append(or_(Equipment.name.ilike(like), Equipment.asset_no.ilike(like),
                         Equipment.location_path.ilike(like),
                         Equipment.system_id.in_(sys_match)))
    where = and_(*conds) if conds else text("true")
    total = (await db.execute(select(func.count()).select_from(Equipment).where(where))).scalar()
    rows = list((await db.execute(
        select(Equipment).where(where)
        .order_by(*(sort_clauses(sort, dir, EQUIPMENT_SORTABLE,
                                 tiebreak=[Equipment.name, Equipment.id])
                    or [Equipment.name, Equipment.asset_no, Equipment.id]))
        .offset(offset).limit(limit))).scalars())
    return EquipmentPage(items=await _out(db, rows), total=total or 0, scope_path=scope)


@router.get("/summary", response_model=EquipmentSummary)
async def summary(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """In-service machines counted by System — the boxes across the register
    (EQ-01, EQ-05, EQ-06). Largest first; machines with no System last."""
    rows = (await db.execute(
        select(Equipment.system_id, VocabularyItem.label, VocabularyItem.is_active, func.count())
        .outerjoin(VocabularyItem, VocabularyItem.id == Equipment.system_id)
        .where(Equipment.is_active.is_(True))
        .group_by(Equipment.system_id, VocabularyItem.label, VocabularyItem.is_active)
    )).all()
    systems = [SystemCount(system_id=sid, label=label, count=n, is_active=bool(active))
               for sid, label, active, n in rows if sid]
    systems.sort(key=lambda r: (-r.count, r.label.lower()))
    unassigned = sum(n for sid, _l, _a, n in rows if sid is None)
    if unassigned:
        systems.append(SystemCount(system_id=None, label="No System", count=unassigned))
    retired = (await db.execute(select(func.count()).select_from(Equipment)
                                .where(Equipment.is_active.is_(False)))).scalar() or 0
    return EquipmentSummary(total=sum(r.count for r in systems), retired=retired,
                            systems=systems)


@router.get("/lookup", response_model=list[EquipmentOut])
async def lookup(
    db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    ids: list[uuid.UUID] = Query(default=[], max_length=PAGE_MAX),
):
    """The named machines, retired ones included — what a record shows for the
    one it names. Pickers used to find that in the first 500 rows they had
    loaded, so a record naming machine 501 said "not in the register"."""
    if not ids:
        return []
    return await _out(db, list((await db.execute(
        select(Equipment).where(Equipment.id.in_(ids)))).scalars()))


TEMPLATE_COLUMNS = ["asset_no", "name", "description", "system", "area", "department",
                    "manufacturer", "model", "serial_no"]


@router.get("/template.csv")
async def template(db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """EQ-04: "provide an equipment csv template to download … including the
    area and the line." One example row; the area is written as the full path
    the register shows, because that is what the upload matches on."""
    example_area = (await db.execute(
        select(Location.path).where(Location.is_active.is_(True))
        .order_by(Location.depth.desc(), Location.path).limit(1))).scalar() or "Production/Line 1"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TEMPLATE_COLUMNS)
    w.writerow(["EQ-0001", "Example filler", "What it is and what it does", "Filler",
                example_area, "", "Maker", "Model 100", "SN-123"])
    return Response(
        content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="equipment-template.csv"'})


@router.get("/{equipment_id}", response_model=EquipmentOut)
async def get_equipment(equipment_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    row = await db.get(Equipment, equipment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipment not found")
    return (await _out(db, [row]))[0]


async def _apply(db: SessionDep, row: Equipment, fields: dict) -> None:
    if "system_id" in fields:
        item = await _system(db, fields["system_id"])
        if item is not None and not item.is_active and item.id != row.system_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"The System '{item.label}' has been retired; pick another")
        row.equipment_type = item.label[:100] if item else None
    for field, value in fields.items():
        setattr(row, field, value)
    await _fill_place(db, row)


@router.post("", response_model=EquipmentOut, status_code=201)
async def create_equipment(payload: EquipmentIn, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    row = Equipment(id=uuid.uuid4(), name=payload.name)
    await _apply(db, row, payload.model_dump())
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return (await _out(db, [row]))[0]


@router.patch("/{equipment_id}", response_model=EquipmentOut)
async def update_equipment(
    equipment_id: uuid.UUID, payload: EquipmentIn, db: SessionDep, user: CanManageSettings,  # noqa: ARG001
):
    row = await db.get(Equipment, equipment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipment not found")
    # ★ Only what was sent. The form sends some fields; writing the whole model
    # blanked manufacturer, model, serial, description and department on every
    # save, and put a retired machine back in service.
    await _apply(db, row, payload.model_dump(exclude_unset=True))
    await db.flush()
    await db.refresh(row)
    return (await _out(db, [row]))[0]


@router.post("/{equipment_id}/retire", response_model=EquipmentOut)
async def retire(equipment_id: uuid.UUID, db: SessionDep, user: CanManageSettings):
    """Q3: "a Retire button that hides it from pickers and keeps the history"."""
    row = await db.get(Equipment, equipment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipment not found")
    if row.is_active:
        row.is_active = False
        row.retired_at = datetime.now(timezone.utc)
        row.retired_by_name = (await db.execute(
            select(User.full_name).where(User.id == _user_id(user)))).scalar()
        await db.flush()
    return (await _out(db, [row]))[0]


@router.post("/{equipment_id}/reinstate", response_model=EquipmentOut)
async def reinstate(equipment_id: uuid.UUID, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    row = await db.get(Equipment, equipment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipment not found")
    row.is_active, row.retired_at, row.retired_by_name = True, None, None
    await db.flush()
    return (await _out(db, [row]))[0]


# ★ Every column that names a machine. A new one belongs here, or a machine a
# record names can be deleted and the record silently loses it (the foreign
# keys are ON DELETE SET NULL).
_REFERENCES: tuple[tuple[str, str], ...] = (
    ("incidents", "select count(*) from ehs_incidents where equipment_id = :id"),
    ("hazards", "select count(*) from ehs_hazards where equipment_id = :id"),
    ("job analyses", "select count(*) from ehs_jsa where equipment_id = :id"),
    ("work permits", "select count(*) from ehs_permits where equipment_id = :id"),
)


async def references(db: SessionDep, equipment_id: uuid.UUID) -> list[tuple[str, int]]:
    out = []
    for what, sql in _REFERENCES:
        n = (await db.execute(text(sql), {"id": equipment_id})).scalar() or 0
        if n:
            out.append((what, n))
    return out


@router.delete("/{equipment_id}", status_code=204)
async def delete_equipment(equipment_id: uuid.UUID, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Q3: "a true delete is allowed only when nothing refers to it"."""
    row = await db.get(Equipment, equipment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipment not found")
    used = await references(db, equipment_id)
    if used:
        named = ", ".join(f"{n} {what}" for what, n in used)
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{row.name} is named on {named}. Retire it instead — "
                            "it leaves the pickers and those records keep it.")
    await db.delete(row)
    await db.flush()
    return Response(status_code=204)


class MergeIn(BaseModel):
    into_id: uuid.UUID


class MergeOut(BaseModel):
    moved: int


@router.post("/systems/{system_id}/merge", response_model=MergeOut)
async def merge_system(system_id: uuid.UUID, payload: MergeIn, db: SessionDep,
                       user: CanManageSettings):  # noqa: ARG001
    """Move every machine in one System into another, and retire the first.

    Q1: "HSE merges them into the groups you want." Done by hand that is a
    form per machine — 2,518 for Valve. The emptied System is retired, not
    deleted, like every list entry.
    """
    source = await _system(db, system_id)
    target = await _system(db, payload.into_id)
    if source.id == target.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick a different System")
    if not target.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"The System '{target.label}' has been retired")
    # ★ Through the ORM, not a Core update: each machine's change log and
    # updated_at then say it was moved (B2 review ③). 2,518 rows is fine.
    machines = list((await db.execute(
        select(Equipment).where(Equipment.system_id == source.id))).scalars())
    for machine in machines:
        machine.system_id, machine.equipment_type = target.id, target.label[:100]
    source.is_active = False
    # Remembered so a later import that still says the old name follows it
    # here instead of putting machines back into a retired System (review ①).
    source.attrs = {**(source.attrs or {}), "merged_into": str(target.id)}
    await db.flush()
    return MergeOut(moved=len(machines))


@router.post("/import", response_model=ImportResult)
async def import_equipment(file: UploadFile, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Load or refresh the register from a CSV export of HSE's spreadsheet.

    CSV rather than xlsx: ehs-api has no spreadsheet library, and adding one to
    read a file somebody exports in two clicks is a dependency that has to be
    installed in every image forever. The columns are matched case-insensitively
    so a sheet with "Asset No" or "asset_no" both work; the template
    (`/template.csv`) has every column this reads.

    ★ Areas are matched on their full path ("Production/103 – Dryer/Line 1").
    Matching on the name alone put machines on the wrong "Line 1" — the plant
    has four. A bare name is still accepted when exactly one area has it, and a
    name several areas share is reported, not guessed.

    A System the list does not have yet is added to it and reported, so a new
    sheet loads in one go; HSE tidy the list afterwards.
    """
    from app.crud.vocabulary import _derive_code

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

    by_path: dict[str, tuple[uuid.UUID, str]] = {}
    by_name: dict[str, list[tuple[uuid.UUID, str]]] = {}
    for lid, name, path in (await db.execute(
            select(Location.id, Location.name, Location.path))).all():
        by_path[(path or "").strip().lower()] = (lid, path)
        by_name.setdefault((name or "").strip().lower(), []).append((lid, path))
    departments = {
        (name or "").lower(): did
        for did, name in (await db.execute(
            select(Department.id, Department.name))).all()
    }
    every_system = {
        sid: (label, active, (attrs or {}).get("merged_into"))
        for sid, label, active, attrs in (await db.execute(
            select(VocabularyItem.id, VocabularyItem.label, VocabularyItem.is_active,
                   VocabularyItem.attrs)
            .where(VocabularyItem.vocabulary_code == SYSTEMS))).all()
    }

    def live(sid: uuid.UUID) -> uuid.UUID | None:
        """The System a name now means: itself, or where it was merged to."""
        seen: set[uuid.UUID] = set()
        while sid not in seen:
            seen.add(sid)
            label, active, into = every_system[sid]
            if active:
                return sid
            try:
                sid = uuid.UUID(into) if into else None
            except ValueError:
                sid = None
            if sid is None or sid not in every_system:
                return None
        return None

    # ★ Active names win over a retired one spelt the same (B2 review ①).
    systems: dict[str, tuple[uuid.UUID, str] | None] = {}
    for sid, (label, active, _into) in sorted(every_system.items(), key=lambda kv: kv[1][1]):
        target = live(sid)
        if target is not None:
            systems[label.lower()] = (target, every_system[target][0])
        else:
            systems.setdefault(label.lower(), None)
    new_systems: list[str] = []

    created = updated = skipped = 0
    problems: list[str] = []

    for i, csv_row in enumerate(reader, start=2):
        name = pick(csv_row, "name", "equipment", "equipment_name", "asset_name")
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
        row.description = pick(csv_row, "description", "notes") or row.description
        row.manufacturer = pick(csv_row, "manufacturer", "make") or row.manufacturer
        row.model = pick(csv_row, "model") or row.model
        row.serial_no = pick(csv_row, "serial", "serial_no", "serial_number") or row.serial_no

        system = pick(csv_row, "system", "kind", "type", "equipment_type")
        if system:
            hit = systems.get(system.lower())
            if hit is None and system.lower() in systems:
                # Retired without being merged anywhere: say so, change nothing.
                problems.append(f"Row {i}: the System '{system}' has been retired; "
                                f"the machine keeps the System it had")
            elif hit is None:
                item = VocabularyItem(
                    id=uuid.uuid4(), vocabulary_code=SYSTEMS, parent_id=None,
                    code=await _derive_code(db, SYSTEMS, system), label=system[:200],
                    sort_order=0, is_active=True, attrs={}, path=system[:200])
                db.add(item)
                await db.flush()
                hit = systems[system.lower()] = (item.id, item.label)
                new_systems.append(item.label)
            if hit is not None:
                row.system_id, row.equipment_type = hit[0], hit[1][:100]

        area = pick(csv_row, "area", "location", "plant_area")
        if area:
            hit = by_path.get(area.lower())
            if hit is None:
                named = by_name.get(area.lower(), [])
                if len(named) == 1:
                    hit = named[0]
                elif named:
                    problems.append(
                        f"Row {i}: {len(named)} areas are called '{area}' — write the full "
                        f"path, e.g. '{named[0][1]}'")
                else:
                    problems.append(f"Row {i}: no area called '{area}'")
            if hit:
                row.location_id, row.location_path = hit

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
                        problems=problems[:50], new_systems=new_systems)
