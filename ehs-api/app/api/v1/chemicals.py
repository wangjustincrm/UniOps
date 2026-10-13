"""The chemical inventory and its safety data sheets.

Reading is gated on `ehs.document.read`, which every active role holds. That is
not a convenience: WHMIS gives a worker the right of access to the data sheet
for a product they work with, and a permission that could deny it would be a
compliance defect rather than a tightening. Managing the inventory sits with
`ehs.document.manage`, the same key as the policy library — it is the same
librarian's job.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import BearerToken, SessionDep
from app.core.permissions import CanManageDocument, CanReadDocument
from app.crud import chemical as crud
from app.services.attachments import MAX_FILE_SIZE, proxy_download, upload_to_file_server

router = APIRouter()

# Suppliers send PDFs. Images are allowed because a laminated sheet on a wall
# is often photographed rather than sourced again.
ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


class HazardClass(BaseModel):
    id: uuid.UUID | None = None
    label: str = Field(max_length=120)


class ChemicalIn(BaseModel):
    name: str = Field(min_length=1, max_length=250)
    product_name: str | None = Field(default=None, max_length=250)
    manufacturer: str | None = Field(default=None, max_length=200)
    supplier: str | None = Field(default=None, max_length=200)
    manufacturer_contact: str | None = Field(default=None, max_length=300)
    product_code: str | None = Field(default=None, max_length=80)
    # A product is routinely several of these at once; picking the worst and
    # dropping the rest loses what a first aider needs.
    hazard_classes: list[HazardClass] = Field(default_factory=list)
    signal_word: str | None = Field(default=None, pattern="^(Danger|Warning)$")
    physical_state: str | None = Field(default=None, max_length=20)
    location_id: uuid.UUID | None = None
    storage_note: str | None = Field(default=None, max_length=300)
    container_size: str | None = Field(default=None, max_length=60)
    quantity_on_site: Decimal | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=20)
    ppe_required: str | None = None
    first_aid_note: str | None = None
    notes: str | None = None


class ChemicalPatch(ChemicalIn):
    name: str | None = Field(default=None, max_length=250)


class SheetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chemical_id: uuid.UUID
    revision: str | None
    revision_date: date | None
    received_on: date | None
    review_due_on: date | None
    file_id: uuid.UUID | None
    filename: str | None
    language: str
    is_current: bool
    qr_token: str | None
    uploaded_by_name: str | None
    created_at: datetime


class ChemicalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chemical_no: str
    name: str
    product_name: str | None
    manufacturer: str | None
    supplier: str | None
    manufacturer_contact: str | None = None
    product_code: str | None
    hazard_classes: list
    signal_word: str | None
    physical_state: str | None
    location_id: uuid.UUID | None
    location_path: str | None
    department_name: str | None
    storage_note: str | None
    container_size: str | None
    quantity_on_site: Decimal | None
    unit: str | None
    ppe_required: str | None
    first_aid_note: str | None
    notes: str | None
    is_active: bool
    # Folded in so a list row can show "no data sheet" without a call per row —
    # which is the whole point of the list.
    current_sheet: SheetOut | None = None


class SheetIn(BaseModel):
    revision: str | None = Field(default=None, max_length=40)
    # Reg 860's three years run from the supplier's revision date, not from
    # the day it was filed.
    revision_date: date | None = None
    received_on: date | None = None
    language: str = Field(default="en", max_length=10)


class GapsOut(BaseModel):
    products: int
    with_current_sheet: int
    no_sheet: list[dict]
    review_overdue: list[dict]


class HazardCount(BaseModel):
    label: str
    count: int


async def _with_sheet(db, chemicals) -> list[ChemicalOut]:
    out: list[ChemicalOut] = []
    for chemical in chemicals:
        item = ChemicalOut.model_validate(chemical)
        sheet = await crud.current_sheet(db, chemical.id)
        item.current_sheet = SheetOut.model_validate(sheet) if sheet else None
        out.append(item)
    return out


@router.get("/chemicals", response_model=list[ChemicalOut])
async def list_chemicals(
    db: SessionDep, user: CanReadDocument,  # noqa: ARG001
    search: str | None = Query(default=None, description="Name, product or supplier code"),
    location_id: uuid.UUID | None = None,
    include_retired: bool = False,
):
    """Searchable by name, or narrowed to an area — both asked standing in
    front of a drum."""
    return await _with_sheet(db, await crud.list_chemicals(
        db, search=search, location_id=location_id, include_retired=include_retired))


@router.get("/chemicals/gaps", response_model=GapsOut)
async def compliance_gaps(db: SessionDep, user: CanReadDocument):  # noqa: ARG001
    """What an inspector writes up: products with no sheet, and sheets past
    their three-year review."""
    return await crud.compliance_gaps(db)


@router.get("/chemicals/by-hazard", response_model=list[HazardCount])
async def by_hazard(db: SessionDep, user: CanReadDocument):  # noqa: ARG001
    """What is stored here, by hazard — the question a fire crew asks."""
    return await crud.by_hazard_class(db)


@router.post("/chemicals", response_model=ChemicalOut, status_code=status.HTTP_201_CREATED)
async def create_chemical(payload: ChemicalIn, db: SessionDep, user: CanManageDocument):  # noqa: ARG001
    data = payload.model_dump()
    data["hazard_classes"] = [
        {"id": str(h["id"]) if h.get("id") else None, "label": h["label"]}
        for h in data.get("hazard_classes") or []
    ]
    chemical = await crud.create_chemical(db, data)
    return (await _with_sheet(db, [chemical]))[0]


@router.get("/chemicals/{chemical_id}", response_model=ChemicalOut)
async def get_chemical(chemical_id: uuid.UUID, db: SessionDep, user: CanReadDocument):  # noqa: ARG001
    return (await _with_sheet(db, [await crud.get_chemical(db, chemical_id)]))[0]


@router.patch("/chemicals/{chemical_id}", response_model=ChemicalOut)
async def update_chemical(
    chemical_id: uuid.UUID, payload: ChemicalPatch, db: SessionDep, user: CanManageDocument,  # noqa: ARG001
):
    chemical = await crud.get_chemical(db, chemical_id)
    fields = payload.model_dump(exclude_unset=True)
    if "hazard_classes" in fields:
        fields["hazard_classes"] = [
            {"id": str(h["id"]) if h.get("id") else None, "label": h["label"]}
            for h in fields["hazard_classes"] or []
        ]
    return (await _with_sheet(db, [await crud.update_chemical(db, chemical, fields)]))[0]


@router.post("/chemicals/{chemical_id}/retire", response_model=ChemicalOut)
async def retire_chemical(chemical_id: uuid.UUID, db: SessionDep, user: CanManageDocument):
    chemical = await crud.get_chemical(db, chemical_id)
    # The user is passed on because retiring discharges the sheet's review
    # clock, and who discharged an obligation is part of the record.
    retired = await crud.retire(db, chemical, user_id=_user_id(user))
    return (await _with_sheet(db, [retired]))[0]


# ── Data sheets ──────────────────────────────────────────────────────────────

@router.get("/chemicals/{chemical_id}/sds", response_model=list[SheetOut])
async def list_sheets(chemical_id: uuid.UUID, db: SessionDep, user: CanReadDocument):  # noqa: ARG001
    """Every revision, current one first. The superseded ones stay because the
    sheet in force at the time of an exposure is part of that record."""
    await crud.get_chemical(db, chemical_id)
    return await crud.sheets_for(db, chemical_id)


@router.post("/chemicals/{chemical_id}/sds", response_model=SheetOut,
             status_code=status.HTTP_201_CREATED)
async def add_sheet(
    chemical_id: uuid.UUID, payload: SheetIn, db: SessionDep, user: CanManageDocument,
):
    """File a revision. Whatever it replaces stops being current, and its
    three-year clock is closed as answered."""
    chemical = await crud.get_chemical(db, chemical_id)
    return await crud.add_sheet(db, chemical, payload.model_dump(), user_id=_user_id(user))


@router.post("/sds/{sds_id}/file", response_model=SheetOut)
async def attach_sheet_file(
    sds_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CanManageDocument, token: BearerToken,  # noqa: ARG001
):
    sheet = await crud.get_sheet(db, sds_id)
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_TYPES:
        raise HTTPException(415, f"Unsupported file type '{ctype}'. Allowed: PDF or an image.")

    sheet.file_id = await upload_to_file_server(
        data, file.filename or "sds.pdf", ctype, "ehs_sds", sheet.id, token)
    sheet.filename = file.filename or "sds.pdf"
    await db.flush()
    return sheet


@router.get("/sds/{sds_id}/file")
async def download_sheet(
    sds_id: uuid.UUID, db: SessionDep, user: CanReadDocument, token: BearerToken,  # noqa: ARG001
):
    """The sheet itself. Every role can reach this, which is the point."""
    sheet = await crud.get_sheet(db, sds_id)
    if sheet.file_id is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This revision has no file attached yet — the product has no usable "
            "data sheet on site")
    return await proxy_download(sheet.file_id, token)
