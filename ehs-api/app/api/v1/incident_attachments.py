"""Photographs and documents attached to an incident.

Visibility follows the incident itself — `_may_read` from `incidents`, so the
person who filed a report can see the photos they took, and someone who can
neither browse the register nor was involved sees nothing. Duplicating that
rule here would be the kind of drift that quietly opens a medical record.
"""
import uuid

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.v1.incidents import _may_read, _user_id
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud import incident as crud
from app.crud.incident import _resolve_name
from app.models.incident import IncidentAttachment
from app.services.attachments import (
    MAX_FILE_SIZE, delete_from_file_server, proxy_download, upload_to_file_server,
)

router = APIRouter(prefix="/incidents/{incident_id}/attachments")

# Only what a phone camera and a scanner produce. The scene photo is the point;
# accepting arbitrary types here would make an incident record an upload target.
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "application/pdf"}


class AttachmentMeta(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    uploaded_by_name: str | None
    created_at: str


def _meta(a: IncidentAttachment) -> AttachmentMeta:
    return AttachmentMeta(
        id=a.id, filename=a.filename, content_type=a.content_type,
        file_size=a.file_size, uploaded_by_name=a.uploaded_by_name,
        created_at=a.created_at.isoformat(),
    )


async def _incident_or_403(db, incident_id: uuid.UUID, user: dict):
    incident = await crud.get(db, incident_id)
    if not await _may_read(db, user, incident):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only open incidents you reported or were involved in",
        )
    return incident


@router.get("", response_model=list[AttachmentMeta])
async def list_attachments(incident_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    await _incident_or_403(db, incident_id, user)
    rows = (await db.execute(
        select(IncidentAttachment)
        .where(IncidentAttachment.incident_id == incident_id)
        .order_by(IncidentAttachment.created_at)
    )).scalars().all()
    return [_meta(r) for r in rows]


@router.post("", response_model=AttachmentMeta, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    incident_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CurrentUserPayload, token: BearerToken,
):
    await _incident_or_403(db, incident_id, user)

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_TYPES:
        raise HTTPException(415, f"Unsupported file type '{ctype}'. Allowed: images and PDF.")

    storage_key = await upload_to_file_server(
        data, file.filename or "photo.jpg", ctype, "ehs_inc", incident_id, token,
    )
    att = IncidentAttachment(
        id=uuid.uuid4(), incident_id=incident_id, storage_key=storage_key,
        filename=file.filename or "photo.jpg", content_type=ctype, file_size=len(data),
        uploaded_by=_user_id(user),
        uploaded_by_name=await _resolve_name(db, _user_id(user)),
    )
    db.add(att)
    await db.flush()
    await db.refresh(att)
    return _meta(att)


@router.get("/{attachment_id}/download")
async def download_attachment(
    incident_id: uuid.UUID, attachment_id: uuid.UUID,
    db: SessionDep, user: CurrentUserPayload, token: BearerToken,
):
    await _incident_or_403(db, incident_id, user)
    att = await db.get(IncidentAttachment, attachment_id)
    if att is None or att.incident_id != incident_id:
        raise HTTPException(404, "Attachment not found")
    return await proxy_download(att.storage_key, token)


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    incident_id: uuid.UUID, attachment_id: uuid.UUID,
    db: SessionDep, user: CurrentUserPayload, token: BearerToken,
):
    await _incident_or_403(db, incident_id, user)
    att = await db.get(IncidentAttachment, attachment_id)
    if att is None or att.incident_id != incident_id:
        raise HTTPException(404, "Attachment not found")
    await delete_from_file_server(att.storage_key, token)
    await db.delete(att)
