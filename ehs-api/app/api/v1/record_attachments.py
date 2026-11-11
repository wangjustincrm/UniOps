"""Photographs on hazards, JSAs and permits (round 12, HAZ-03 and JSA-06).

Who may see and add them follows the record, not a key of their own:

* a hazard — read with `ehs.action.read` (and, for someone who cannot write
  hazards, only once it has been assessed: the register shows them nothing
  before that); added and removed with `ehs.action.write`;
* a JSA — read by everyone who can open one (`ehs.incident.report`); added
  with `ehs.action.write`, as the JSA itself is written;
* a permit — as the permit itself: seen by whoever raised it or holds the
  register (`ehs.permit.read`), added by whoever raised it or issues permits.
  ★ The key alone was checked, which every employee holds, so anyone with a
  permit's id could list, download, add and remove its photographs while the
  permit itself answered 403 (B3/B4 review by uniops-95, H1). Photographs are
  added until the permit is closed out and removed only while it is a draft
  or suspended, the same window in which it can be edited: what a permit
  showed when it was issued and closed is evidence.

A file is removed only by whoever added it or by someone who may write the
record: a crew member's photograph of a guard is not someone else's to delete.
"""
import uuid

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from uniops_authz import has_permission

from app.api.v1.incidents import _user_id
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud.incident import _resolve_name
from app.models.record_attachment import RecordAttachment
from app.services.attachments import (
    MAX_FILE_SIZE, delete_from_file_server, proxy_download, upload_to_file_server,
)

router = APIRouter(prefix="/records/{owner_type}/{owner_id}/attachments")

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "application/pdf"}

# owner_type -> (read key, write key, file-api doc type)
OWNERS = {
    "hazard": ("ehs.action.read", "ehs.action.write", "ehs_haz"),
    "jsa": ("ehs.incident.report", "ehs.action.write", "ehs_jsa"),
    "permit": ("ehs.permit.request", "ehs.permit.request", "ehs_ptw"),
}


class AttachmentMeta(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    uploaded_by_name: str | None
    created_at: str


def _meta(a: RecordAttachment) -> AttachmentMeta:
    return AttachmentMeta(id=a.id, filename=a.filename, content_type=a.content_type,
                          file_size=a.file_size, uploaded_by_name=a.uploaded_by_name,
                          created_at=a.created_at.isoformat())


async def _can(db, user: dict, key: str) -> bool:
    return await has_permission(db, _user_id(user), user.get("role", ""), key)


_PERMIT_FINISHED = ("closed", "cancelled", "expired")
_PERMIT_EDITABLE = ("draft", "suspended")


async def _owner(db, user: dict, owner_type: str, owner_id: uuid.UUID, *, write: bool,
                 removing: bool = False) -> None:
    """The record exists and this person may read it (or write to it)."""
    if owner_type not in OWNERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such kind of record")
    read_key, write_key, _ = OWNERS[owner_type]
    if not await _can(db, user, write_key if write else read_key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot do that on this record")
    if owner_type == "hazard":
        from app.crud import hazard as crud
        hazard = await crud.get_hazard(db, owner_id)
        if not write and hazard.risk_assessment_id is None and not await _can(db, user, write_key):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Hazard not found")
    elif owner_type == "jsa":
        from app.crud import hazard as crud
        await crud.get_jsa(db, owner_id)
    else:
        from app.api.v1.permits import _editable, _readable
        from app.crud import permit as crud
        permit = await crud.get_permit(db, owner_id)
        await _readable(db, user, permit)
        if write:
            await _editable(db, user, permit)
            if crud.effective(permit) in _PERMIT_FINISHED:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    f"{permit.permit_no} is {crud.effective(permit)}: "
                                    "its photographs are part of the record now")
        if removing and crud.effective(permit) not in _PERMIT_EDITABLE:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"{permit.permit_no} is {crud.effective(permit)}: a photograph "
                                "can be removed only while it is a draft or suspended")


@router.get("", response_model=list[AttachmentMeta])
async def list_attachments(owner_type: str, owner_id: uuid.UUID, db: SessionDep,
                           user: CurrentUserPayload):
    await _owner(db, user, owner_type, owner_id, write=False)
    rows = (await db.execute(
        select(RecordAttachment)
        .where(RecordAttachment.owner_type == owner_type, RecordAttachment.owner_id == owner_id)
        .order_by(RecordAttachment.created_at))).scalars().all()
    return [_meta(r) for r in rows]


@router.post("", response_model=AttachmentMeta, status_code=status.HTTP_201_CREATED)
async def upload_attachment(owner_type: str, owner_id: uuid.UUID, file: UploadFile,
                            db: SessionDep, user: CurrentUserPayload, token: BearerToken):
    await _owner(db, user, owner_type, owner_id, write=True)
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_TYPES:
        raise HTTPException(415, f"Unsupported file type '{ctype}'. Allowed: images and PDF.")
    name = file.filename or "photo.jpg"
    key = await upload_to_file_server(data, name, ctype, OWNERS[owner_type][2], owner_id, token)
    att = RecordAttachment(
        id=uuid.uuid4(), owner_type=owner_type, owner_id=owner_id, storage_key=key,
        filename=name, content_type=ctype, file_size=len(data),
        uploaded_by=_user_id(user), uploaded_by_name=await _resolve_name(db, _user_id(user)))
    db.add(att)
    await db.flush()
    await db.refresh(att)
    return _meta(att)


async def _one(db, owner_type: str, owner_id: uuid.UUID, attachment_id: uuid.UUID):
    att = await db.get(RecordAttachment, attachment_id)
    if att is None or att.owner_type != owner_type or att.owner_id != owner_id:
        raise HTTPException(404, "Attachment not found")
    return att


@router.get("/{attachment_id}/download")
async def download_attachment(owner_type: str, owner_id: uuid.UUID, attachment_id: uuid.UUID,
                              db: SessionDep, user: CurrentUserPayload, token: BearerToken):
    await _owner(db, user, owner_type, owner_id, write=False)
    return await proxy_download((await _one(db, owner_type, owner_id, attachment_id)).storage_key,
                                token)


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(owner_type: str, owner_id: uuid.UUID, attachment_id: uuid.UUID,
                            db: SessionDep, user: CurrentUserPayload, token: BearerToken):
    await _owner(db, user, owner_type, owner_id, write=False, removing=True)
    att = await _one(db, owner_type, owner_id, attachment_id)
    if att.uploaded_by != _user_id(user):
        # Someone else's photograph: only whoever may write the record. For a
        # permit the key is held by everyone, so ask the permit's own rule.
        try:
            await _owner(db, user, owner_type, owner_id, write=True)
        except HTTPException as e:
            if e.status_code != status.HTTP_403_FORBIDDEN:
                raise
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Only whoever added it can remove it") from None
    await delete_from_file_server(att.storage_key, token)
    await db.delete(att)
