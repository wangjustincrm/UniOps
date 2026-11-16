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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from app.core.deps import BearerToken, SessionDep
from app.core.permissions import (
    CanManageSettings, CanReadDocument, CanReadIncident, CanReportIncident,
)
from app.crud import vocabulary as crud
from app.services import area_scope, mdm, notify
from app.services.attachments import MAX_FILE_SIZE, proxy_download, upload_to_file_server
from app.models.config import EhsConfig
from app.models.area_owner import AreaOwner
from app.models.mirrors import Department, Location, User
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


class ReportingGuidance(BaseModel):
    """What somebody filling in an incident report needs to know, and no more.

    ★ Its own endpoint rather than widening `/settings/config`, which needs
    `ehs.incident.read` — a permission an ordinary employee does not hold, and
    they are exactly who reports incidents. Reached through the config endpoint
    the guidance would have been invisible to everybody who needs it, which is
    this module's most repeated fault. The rest of the config — who is notified,
    the mail templates, the escalation timings — is nobody's business at the
    point of filling in a form, so it is not in here.
    """

    lost_time_guidance: str | None
    allow_anonymous_report: bool


@router.get("/reporting-guidance", response_model=ReportingGuidance)
async def reporting_guidance(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    config = await _config_row(db)
    rules = config.lost_time_rules or {}
    text_value = (rules.get("guidance") or "").strip() or None
    return ReportingGuidance(
        lost_time_guidance=text_value,
        allow_anonymous_report=bool(config.allow_anonymous_report),
    )


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
    everything = await crud.list_items(db, code, include_inactive=True)
    trails = crud.trails(everything)
    return [
        VocabularyItemOut.model_validate(i).model_copy(update={"trail": trails[i.id]})
        for i in everything if include_inactive or i.is_active
    ]


async def _out(db, item) -> VocabularyItemOut:
    return VocabularyItemOut.model_validate(item).model_copy(
        update={"trail": await crud.trail(db, item)})


@router.get("/vocabularies/{code}/items/{item_id}/usage")
async def item_usage(code: str, item_id: uuid.UUID, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """What names this entry. Asked before something is added under it: an
    entry with something under it is a heading and can no longer be chosen,
    and the records already on it stay on it."""
    item = await crud.get_item(db, code, item_id)
    used = await crud.usage(db, item.id)
    return {"total": sum(u["count"] for u in used), "by": used}


@router.post("/vocabularies/{code}/items", response_model=VocabularyItemOut,
             status_code=status.HTTP_201_CREATED)
async def add_item(code: str, payload: VocabularyItemIn, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    return await _out(db, await crud.add_item(db, code, payload))


@router.patch("/vocabularies/{code}/items/{item_id}", response_model=VocabularyItemOut)
async def patch_item(
    code: str, item_id: uuid.UUID, payload: VocabularyItemPatch,
    db: SessionDep, user: CanManageSettings,  # noqa: ARG001
):
    return await _out(db, await crud.patch_item(db, code, item_id, payload))


@router.delete("/vocabularies/{code}/items/{item_id}", response_model=VocabularyItemOut)
async def retire_item(code: str, item_id: uuid.UUID, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Retire an entry. It stops appearing on new forms and stays on old ones.

    Deliberately not a hard delete: an entry that has been recorded on an
    investigation has to keep existing, or that investigation stops making
    sense. Returns the retired entry rather than 204 so the caller can see it
    still exists.
    """
    return await _out(db, await crud.retire_item(db, code, item_id))


# ── Plant tree (read here, written through mdm-api, which owns the table) ───


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


class LocationIn(BaseModel):
    """What HSE fills in to add an area.

    No code: they name areas, and a code typed by hand is a code that
    eventually collides with another or contradicts its parent. mdm-api derives
    one from the parent and the name.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    level: str = Field(pattern="^(area|department|line|sub_line|room|workstation)$")
    access_area: str | None = Field(default=None, max_length=30)
    is_selectable: bool = True
    sort_order: int = 0


class LocationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    level: str | None = Field(
        default=None, pattern="^(area|department|line|sub_line|room|workstation)$")
    access_area: str | None = Field(default=None, max_length=30)
    is_selectable: bool | None = None
    sort_order: int | None = None


# The write path is a proxy, not a second implementation: `locations` belongs
# to mdm-api and the maintenance screen belongs to Safety, which is the whole
# reason these three endpoints exist. Permission is applied at the far end
# against the caller's own token — see app/services/mdm.py.
@router.post("/locations", status_code=status.HTTP_201_CREATED)
async def create_location(body: LocationIn, token: BearerToken):
    return await mdm.create_location(
        {k: (str(v) if isinstance(v, uuid.UUID) else v)
         for k, v in body.model_dump().items()}, token)


@router.patch("/locations/{location_id}")
async def update_location(location_id: uuid.UUID, body: LocationPatch, token: BearerToken):
    return await mdm.update_location(location_id, body.model_dump(exclude_unset=True), token)


@router.post("/locations/{location_id}/retire")
async def retire_location(location_id: uuid.UUID, token: BearerToken):
    """Off the list offered for new records; every past record keeps it."""
    return await mdm.set_location_active(location_id, False, token)


@router.post("/locations/{location_id}/restore")
async def restore_location(location_id: uuid.UUID, token: BearerToken):
    return await mdm.set_location_active(location_id, True, token)


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


# ── Who covers which part of the plant ───────────────────────────────────────

class AreaOwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None
    location_id: uuid.UUID
    # The tree's current answer, not the snapshot taken when this was assigned:
    # a renamed area should read as its new name here.
    location_path: str


class AreaOwnerIn(BaseModel):
    user_id: uuid.UUID
    location_id: uuid.UUID


@router.get("/area-owners", response_model=list[AreaOwnerOut])
async def list_area_owners(db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Who is down for which area, worst-case readable: ordered by area.

    Read with the live path so a renamed area reads as its new name rather than
    as whatever it was called when somebody was assigned to it.
    """
    # Left join on purpose: an area deleted from the master data would otherwise
    # take its assignment off this screen while leaving the row in the table,
    # so nobody could clear it.
    rows = (await db.execute(text(
        "select o.id, o.user_id, o.user_name, o.location_id,"
        "       coalesce(l.path, o.location_path || '  (area no longer exists)')"
        "  from ehs_area_owners o"
        "  left join locations l on l.id = o.location_id"
        " order by 5, o.user_name"))).all()
    return [AreaOwnerOut(id=r[0], user_id=r[1], user_name=r[2],
                         location_id=r[3], location_path=r[4]) for r in rows]


@router.post("/area-owners", response_model=AreaOwnerOut, status_code=status.HTTP_201_CREATED)
async def add_area_owner(payload: AreaOwnerIn, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Make somebody responsible for an area, and for everything under it."""
    location = (await db.execute(
        select(Location).where(Location.id == payload.location_id))).scalar_one_or_none()
    if location is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That area is not in the plant tree")
    if not location.is_active:
        # A retired area cannot be made somebody's responsibility. Work already
        # recorded against it keeps falling to HSE, which is where the count
        # will show it.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{location.path} is no longer in use, so it cannot be assigned")

    person = (await db.execute(
        select(User).where(User.id == payload.user_id))).scalar_one_or_none()
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person")
    if not person.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{person.full_name}'s account is not active, so work sent to this "
            "area would reach nobody")

    # ★ Any active member of staff, whatever role they hold. HSE: "the area
    # owners can be assigned to anyone regardless of role. remove the supervisor
    # requirement."
    #
    # This used to refuse anybody without `area_supervisor`, and the refusal was
    # right at the time: area work is addressed to that role and was only ever
    # fetched for holders of it, so an area given to anybody else looked covered
    # and reached nobody. Removing the check alone would have restored exactly
    # that silent loss. What changed with it is the task query — being down for
    # an area is now its own reason to be sent that area's work.

    existing = (await db.execute(
        select(AreaOwner).where(AreaOwner.user_id == payload.user_id,
                                AreaOwner.location_id == payload.location_id)
    )).scalar_one_or_none()
    if existing is not None:
        # Saying it twice is not an error worth stopping for.
        return AreaOwnerOut(id=existing.id, user_id=existing.user_id,
                            user_name=existing.user_name,
                            location_id=existing.location_id,
                            location_path=location.path)

    row = AreaOwner(
        id=uuid.uuid4(), user_id=person.id, user_name=person.full_name,
        location_id=location.id, location_path=location.path,
    )
    db.add(row)
    await db.flush()
    return AreaOwnerOut(id=row.id, user_id=row.user_id, user_name=row.user_name,
                        location_id=row.location_id, location_path=location.path)


@router.delete("/area-owners/{owner_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_area_owner(owner_id: uuid.UUID, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    row = await db.get(AreaOwner, owner_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not assigned")
    await db.delete(row)


# ── Incident types (round 12, GEN-01 and Q10) ────────────────────────────────


class IncidentTypeRow(BaseModel):
    code: str
    label: str
    form_kind: str
    category_id: uuid.UUID | None = None
    category_label: str | None = None
    # Only coordinators, supervisors and HSE may pick it.
    restricted: bool = False
    is_active: bool = True
    # A built-in type: its legal meaning is fixed and it is not edited here.
    builtin: bool = False
    mol_reportable: bool = False
    injury_class: str | None = None


class IncidentTypeIn(BaseModel):
    label: str = Field(min_length=2, max_length=200)
    form_kind: str = Field(pattern="^(medical|equipment|near_miss)$")
    category_id: uuid.UUID | None = None
    restricted: bool = False


class IncidentTypePatch(BaseModel):
    label: str | None = Field(default=None, min_length=2, max_length=200)
    form_kind: str | None = Field(default=None, pattern="^(medical|equipment|near_miss)$")
    category_id: uuid.UUID | None = None
    restricted: bool | None = None
    is_active: bool | None = None


_RESTRICTED = "ehs.incident.investigate"


@router.get("/incident-types", response_model=list[IncidentTypeRow])
async def list_incident_types(db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Every type: the built-in ones, fixed, and HSE's own."""
    from app.services import incident_type as it
    out = []
    for t in it.all_types(include_retired=True):
        out.append(IncidentTypeRow(
            code=t.code, label=t.label, form_kind=t.form_kind,
            category_id=t.category_id, category_label=t.category_label,
            restricted=t.pick_permission is not None,
            is_active=(not t.custom) or t in it.all_types(),
            builtin=not t.custom, mol_reportable=t.mol_reportable,
            injury_class=t.injury_class))
        if not t.custom and t.category_code:
            out[-1].category_label = (await db.execute(text(
                "select label from ehs_vocabulary_items where vocabulary_code = 'incident_category'"
                " and code = :c"), {"c": t.category_code})).scalar()
    return out


async def _category(db, category_id: uuid.UUID | None) -> str | None:
    if category_id is None:
        return None
    label = (await db.execute(text(
        "select label from ehs_vocabulary_items where id = :id"
        " and vocabulary_code = 'incident_category'"), {"id": category_id})).scalar()
    if label is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "That is not an incident category")
    return label


@router.post("/incident-types", response_model=IncidentTypeRow, status_code=status.HTTP_201_CREATED)
async def add_incident_type(payload: IncidentTypeIn, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Q10: "HSE can add new types and set the label, form, category and who
    can pick them, but not the legal flags." There is nothing here to set
    them with: a type added here never sets an injury class or tells the
    Ministry."""
    import re
    from app.models.incident_type import CustomIncidentType
    from app.services import incident_type as it
    base = it.CUSTOM_PREFIX + (re.sub(r"[^a-z0-9]+", "_", payload.label.lower()).strip("_")[:45] or "type")
    code, n = base, 2
    while code in it.BY_CODE:
        code, n = f"{base}_{n}", n + 1
    row = CustomIncidentType(
        id=uuid.uuid4(), code=code, label=payload.label.strip(), form_kind=payload.form_kind,
        category_id=payload.category_id, category_label=await _category(db, payload.category_id),
        pick_permission=_RESTRICTED if payload.restricted else None)
    db.add(row)
    await db.flush()
    await it.load(db)
    return IncidentTypeRow(code=row.code, label=row.label, form_kind=row.form_kind,
                           category_id=row.category_id, category_label=row.category_label,
                           restricted=payload.restricted)


@router.patch("/incident-types/{code}", response_model=IncidentTypeRow)
async def edit_incident_type(code: str, payload: IncidentTypePatch, db: SessionDep,
                             user: CanManageSettings):  # noqa: ARG001
    from app.models.incident_type import CustomIncidentType
    from app.services import incident_type as it
    if code in it.BUILTIN:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "A built-in type is fixed: what it means for the Ministry, WSIB "
                            "and the injury rates is set in the system")
    row = (await db.execute(select(CustomIncidentType).where(
        CustomIncidentType.code == code))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such incident type")
    fields = payload.model_dump(exclude_unset=True)
    if "label" in fields:
        row.label = fields["label"].strip()
    if "form_kind" in fields and fields["form_kind"] != row.form_kind:
        # ★ Review L2: a report is on the form it came in on. Moving a type that
        # reports already use would leave them on one form and their kind on
        # another, and the reclassify rule (same form only) would trap them.
        from app.models.incident import Incident
        used = (await db.execute(select(Incident.id).where(
            Incident.extra["incident_type"].astext == code).limit(1))).first()
        if used:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Reports already use this type, so its form cannot change. "
                                "Retire it and add a new one on the other form.")
        row.form_kind = fields["form_kind"]
    if "category_id" in fields:
        row.category_id = fields["category_id"]
        row.category_label = await _category(db, fields["category_id"])
    if "restricted" in fields:
        row.pick_permission = _RESTRICTED if fields["restricted"] else None
    if "is_active" in fields:
        row.is_active = bool(fields["is_active"])
    await db.flush()
    await it.load(db)
    return IncidentTypeRow(code=row.code, label=row.label, form_kind=row.form_kind,
                           category_id=row.category_id, category_label=row.category_label,
                           restricted=row.pick_permission is not None, is_active=row.is_active)


# ── Email wording (UAT 13) ──────────────────────────────────────────────────
# "Create a way to change the default email templates that are sent to workers
# to ensure proper grammar and sentence structure."

class EmailTemplateOut(BaseModel):
    key: str
    name: str
    group: str
    audience: str
    when: str
    placeholders: dict[str, str]
    default_subject: str
    default_body: str
    subject: str
    body: str
    customised: bool


class EmailTemplateIn(BaseModel):
    subject: str = Field(max_length=300)
    body: str = Field(max_length=10000)


class EmailPreviewOut(BaseModel):
    subject: str
    html: str
    problems: list[str]


def _template_out(cfg: EhsConfig, key: str) -> EmailTemplateOut:
    from app.services import email_templates as et
    t = et.TEMPLATES[key]
    mine = (cfg.email_templates or {}).get(key) or {}
    return EmailTemplateOut(
        key=key, name=t.name, group=t.group, audience=t.audience, when=t.when,
        placeholders=t.placeholders,
        default_subject=t.subject, default_body=t.body,
        subject=mine.get("subject") or t.subject, body=mine.get("body") or t.body,
        customised=bool(mine))


def _known(key: str):
    from app.services import email_templates as et
    if key not in et.TEMPLATES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no such email")


@router.get("/email-templates", response_model=list[EmailTemplateOut])
async def list_email_templates(db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    from app.services import email_templates as et
    cfg = await _config_row(db)
    return [_template_out(cfg, k) for k in et.TEMPLATES]


@router.put("/email-templates/{key}", response_model=EmailTemplateOut)
async def save_email_template(key: str, payload: EmailTemplateIn, db: SessionDep,
                              user: CanManageSettings):  # noqa: ARG001
    """HSE's wording for one email. Refused, saying why, if it uses a
    placeholder the email does not have or a brace that does not pair up."""
    from app.services import email_templates as et
    _known(key)
    found = et.problems(key, payload.subject, payload.body)
    if found:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, " ".join(found))
    cfg = await _config_row(db)
    # A new dict, so the JSONB column is seen as changed.
    cfg.email_templates = {**(cfg.email_templates or {}),
                           key: {"subject": payload.subject.strip(), "body": payload.body.strip()}}
    await db.flush()
    return _template_out(cfg, key)


@router.delete("/email-templates/{key}", response_model=EmailTemplateOut)
async def reset_email_template(key: str, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Back to the default wording."""
    _known(key)
    cfg = await _config_row(db)
    cfg.email_templates = {k: v for k, v in (cfg.email_templates or {}).items() if k != key}
    await db.flush()
    return _template_out(cfg, key)


@router.post("/email-templates/{key}/preview", response_model=EmailPreviewOut)
async def preview_email_template(key: str, payload: EmailTemplateIn,
                                 user: CanManageSettings):  # noqa: ARG001
    """The email as somebody would receive it, with example values, before it
    is saved. Nothing is sent."""
    from app.services import email_templates as et
    from app.services import mail
    _known(key)
    t = et.TEMPLATES[key]
    found = et.problems(key, payload.subject, payload.body)
    if found:
        return EmailPreviewOut(subject="", html="", problems=found)
    subject = et.one_line(et.fill(payload.subject, t.sample, drop_empty_lines=False))
    body = et.fill(payload.body, t.sample).strip()
    links = {t.sample[k]: "#" for k in t.linked if t.sample.get(k)}
    return EmailPreviewOut(subject=subject, problems=[],
                           html=mail.render(body, title=subject, link="#", links=links))
