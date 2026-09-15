"""Settings — the lists and parameters the HSE Manager maintains.

The plant tree is read-only here on purpose. `locations` is master data owned
by mdm-api; Safety is its first consumer and the HSE Manager is the person who
keeps it current, so the maintenance screen belongs in this module — but the
write path stays with the service that owns the table. Reads come from the
local mirror, which is the same physical table.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import BearerToken, SessionDep
from app.core.permissions import (
    CanManageSettings, CanReadDocument, CanReadIncident, CanReportIncident,
)
from app.crud import vocabulary as crud
from app.services.attachments import MAX_FILE_SIZE, proxy_download, upload_to_file_server
from app.models.config import EhsConfig
from app.models.mirrors import Department, Location
from app.schemas.settings import (
    ConfigOut,
    ConfigUpdate,
    LocationNode,
    VocabularyItemIn,
    VocabularyItemOut,
    VocabularyItemPatch,
    VocabularyOut,
)

router = APIRouter()


# ── Behaviour parameters ────────────────────────────────────────────────────


async def _config_row(db) -> EhsConfig:
    row = (await db.execute(select(EhsConfig).where(EhsConfig.id == 1))).scalar_one_or_none()
    if row is None:
        # The seed migration creates it; a missing row means the migration did
        # not run, and saying so beats a 500 three screens later.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Safety settings have not been initialised — migration 20260829_0002 "
            "has not run against this database",
        )
    return row


@router.get("/config", response_model=ConfigOut)
async def get_config(db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    return ConfigOut.model_validate(await _config_row(db))


@router.put("/config", response_model=ConfigOut)
async def update_config(payload: ConfigUpdate, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    row = await _config_row(db)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        setattr(row, field, value)

    # Re-check the ladder against the stored values, not just the submitted
    # ones: raising only the supervisor threshold past the manager's would
    # otherwise pass validation and silently skip a rung.
    if row.capa_escalate_manager_days <= row.capa_escalate_supervisor_days:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The HSE Manager threshold ({row.capa_escalate_manager_days}d) must be "
            f"later than the supervisor threshold ({row.capa_escalate_supervisor_days}d)",
        )
    await db.flush()
    return ConfigOut.model_validate(row)


# ── Vocabularies ────────────────────────────────────────────────────────────


@router.get("/vocabularies", response_model=list[VocabularyOut])
async def list_vocabularies(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """Readable by anyone who can file a report, which is everyone.

    These lists are what a form offers as choices — causes, hazards, PPE. A
    production worker filling in an incident needs them, and gating them behind
    ehs.incident.read left the location picker empty for exactly the people the
    module exists for. Editing is still ehs.settings.manage.
    """
    return [
        VocabularyOut.model_validate(v).model_copy(
            update={"item_count": total, "active_item_count": active})
        for v, total, active in await crud.list_vocabularies(db)
    ]


@router.get("/vocabularies/{code}/items", response_model=list[VocabularyItemOut])
async def list_items(
    code: str,
    db: SessionDep,
    user: CanReportIncident,  # noqa: ARG001
    include_inactive: bool = Query(
        default=False,
        description="Retired entries. Off by default so pickers only offer live ones.",
    ),
):
    await crud.get_vocabulary(db, code)
    return [
        VocabularyItemOut.model_validate(i)
        for i in await crud.list_items(db, code, include_inactive=include_inactive)
    ]


@router.post("/vocabularies/{code}/items", response_model=VocabularyItemOut,
             status_code=status.HTTP_201_CREATED)
async def add_item(code: str, payload: VocabularyItemIn, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    return VocabularyItemOut.model_validate(await crud.add_item(db, code, payload))


@router.patch("/vocabularies/{code}/items/{item_id}", response_model=VocabularyItemOut)
async def patch_item(
    code: str, item_id: uuid.UUID, payload: VocabularyItemPatch,
    db: SessionDep, user: CanManageSettings,  # noqa: ARG001
):
    return VocabularyItemOut.model_validate(await crud.patch_item(db, code, item_id, payload))


@router.delete("/vocabularies/{code}/items/{item_id}", response_model=VocabularyItemOut)
async def retire_item(code: str, item_id: uuid.UUID, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Retire an entry. It stops appearing on new forms and stays on old ones.

    Deliberately not a hard delete: an entry that has been recorded on an
    investigation has to keep existing, or that investigation stops making
    sense. Returns the retired entry rather than 204 so the caller can see it
    still exists.
    """
    return VocabularyItemOut.model_validate(await crud.retire_item(db, code, item_id))


# ── Plant tree (read-only — mdm-api owns the table) ─────────────────────────


# Every incident is filed against an area, so the tree has to be readable by
# whoever is filing — not only by the people who can browse the register.
@router.get("/locations", response_model=list[LocationNode])
async def location_tree(
    db: SessionDep,
    user: CanReportIncident,  # noqa: ARG001
    include_inactive: bool = False,
):
    stmt = select(Location)
    if not include_inactive:
        stmt = stmt.where(Location.is_active.is_(True))
    # HSE's order, not the alphabet's: they put Outside the Plant and
    # Production first because those are what most reports are filed against,
    # and listed the operational areas in process order. `path` as the
    # tie-break keeps siblings stable where nothing was specified.
    rows = list((await db.execute(
        stmt.order_by(Location.depth, Location.sort_order, Location.path))).scalars())

    nodes = {r.id: LocationNode.model_validate(r) for r in rows}
    roots: list[LocationNode] = []
    for row in rows:
        node = nodes[row.id]
        parent = nodes.get(row.parent_id) if row.parent_id else None
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)
    return roots


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """Departments, from the mdm mirror.

    Served here so the inspection rotation can be built in the UI. Read-only —
    departments are mdm's, and a second place to edit them would drift.
    """
    return list((await db.execute(
        select(Department).where(Department.is_active.is_(True)).order_by(Department.code)
    )).scalars().all())


# ── GHS pictogram artwork ────────────────────────────────────────────────────
# The nine hazard-class pictograms are regulated artwork: a red diamond with a
# specific symbol, published by Health Canada for WHMIS 2015. They are not
# drawn here and never should be — an approximation of a regulated symbol on a
# drum is a worse failure than an obviously missing one, because it looks
# right. The artwork is uploaded once, against the hazard class it belongs to,
# and the label composes with it.
#
# It lives in the vocabulary item's `attrs` rather than a table of its own:
# `attrs` is the extension point these items were given, the artwork is an
# attribute of the class and of nothing else, and a class retired from the list
# takes its artwork out of circulation with it.
PICTOGRAM_VOCABULARY = "ghs_hazard_class"
# Raster only. An SVG would need a renderer this service does not carry, and a
# pictogram that fails to draw at print time is the one failure mode there is
# no recovering from — the label goes on the drum either way.
PICTOGRAM_TYPES = {"image/png", "image/jpeg", "image/webp"}


async def _pictogram_item(db, code: str, item_id: uuid.UUID):
    if code != PICTOGRAM_VOCABULARY:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Pictograms belong to '{PICTOGRAM_VOCABULARY}' — no other list prints one",
        )
    return await crud.get_item(db, code, item_id)


@router.post("/vocabularies/{code}/items/{item_id}/pictogram",
             response_model=VocabularyItemOut)
async def upload_pictogram(
    code: str, item_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CanManageSettings, token: BearerToken,  # noqa: ARG001
):
    """Attach the official artwork for one hazard class."""
    item = await _pictogram_item(db, code, item_id)
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in PICTOGRAM_TYPES:
        raise HTTPException(
            415,
            f"Unsupported file type '{ctype}'. A pictogram must be a PNG, JPEG or WebP "
            "image — PNG with a transparent background prints best.",
        )

    file_id = await upload_to_file_server(
        data, file.filename or "pictogram.png", ctype, "ehs_vocab", item.id, token)
    # Replace the whole dict: SQLAlchemy does not see a mutation in place on a
    # JSONB column, and a pictogram that silently failed to save is exactly the
    # kind of thing nobody notices until a label prints without it.
    item.attrs = {**(item.attrs or {}),
                  "pictogram_file_id": str(file_id),
                  "pictogram_filename": file.filename or "pictogram.png"}
    await db.flush()
    return item


@router.delete("/vocabularies/{code}/items/{item_id}/pictogram",
               response_model=VocabularyItemOut)
async def remove_pictogram(
    code: str, item_id: uuid.UUID, db: SessionDep, user: CanManageSettings,  # noqa: ARG001
):
    item = await _pictogram_item(db, code, item_id)
    attrs = {k: v for k, v in (item.attrs or {}).items()
             if k not in ("pictogram_file_id", "pictogram_filename")}
    item.attrs = attrs
    await db.flush()
    return item


@router.get("/vocabularies/{code}/items/{item_id}/pictogram")
async def download_pictogram(
    code: str, item_id: uuid.UUID,
    db: SessionDep, user: CanReadDocument, token: BearerToken,  # noqa: ARG001
):
    """The artwork itself. Readable by everyone, like the data sheets."""
    item = await _pictogram_item(db, code, item_id)
    file_id = (item.attrs or {}).get("pictogram_file_id")
    if not file_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No pictogram has been uploaded for {item.label}",
        )
    return await proxy_download(uuid.UUID(str(file_id)), token)
