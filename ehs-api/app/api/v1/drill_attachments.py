"""Photographs taken at an emergency drill.

A drill evaluation used to be prose — what worked, what did not. HSE asked for
the muster point itself: whether people actually gathered where the plan says,
whether the exit that was meant to be clear was. A sentence saying the north
door was blocked and a photograph of the north door blocked are not the same
evidence, and it is the second one an inspector asks for.

Visibility follows the drill register rather than being invented here: anyone
who may read drills may see their photographs, and only whoever may record a
drill may add or remove one.
"""
import uuid

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.v1.drills import _user_id
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.core.permissions import CanReadIncident, CanWriteFirstAid
from app.crud import drill as crud
from app.crud.incident import _resolve_name
from app.models.drill import DrillAttachment
from app.services.attachments import (
    MAX_FILE_SIZE, delete_from_file_server, proxy_download, upload_to_file_server,
)

router = APIRouter(prefix="/drills/{drill_id}/attachments")

# What a phone camera and a scanner produce, and nothing else: a drill record
# is not somewhere to park arbitrary files.
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "application/pdf"}


class AttachmentMeta(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    uploaded_by_name: str | None
    created_at: str


def _meta(a: DrillAttachment) -> AttachmentMeta:
    return AttachmentMeta(
        id=a.id, filename=a.filename, content_type=a.content_type,
        file_size=a.file_size, uploaded_by_name=a.uploaded_by_name,
        created_at=a.created_at.isoformat(),
    )


@router.get("", response_model=list[AttachmentMeta])
async def list_attachments(
    drill_id: uuid.UUID, db: SessionDep, user: CanReadIncident,  # noqa: ARG001
):
    await crud.get_drill(db, drill_id)
    rows = (await db.execute(
        select(DrillAttachment)
        .where(DrillAttachment.drill_id == drill_id)
        .order_by(DrillAttachment.created_at)
    )).scalars().all()
    return [_meta(r) for r in rows]


@router.post("", response_model=AttachmentMeta, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    drill_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CanWriteFirstAid, token: BearerToken,
):
    # Same gate the drill's own fields carry. Without it a photograph could be
    # added to a drill that was signed off and closed weeks ago.
    crud.assert_not_evaluated(await crud.get_drill(db, drill_id), "its evidence")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_TYPES:
        raise HTTPException(415, f"Unsupported file type '{ctype}'. Allowed: images and PDF.")

    storage_key = await upload_to_file_server(
        data, file.filename or "photo.jpg", ctype, "ehs_drl", drill_id, token,
    )
    att = DrillAttachment(
        id=uuid.uuid4(), drill_id=drill_id, storage_key=storage_key,
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
    drill_id: uuid.UUID, attachment_id: uuid.UUID,
    db: SessionDep, user: CanReadIncident, token: BearerToken,  # noqa: ARG001
):
    await crud.get_drill(db, drill_id)
    att = await db.get(DrillAttachment, attachment_id)
    if att is None or att.drill_id != drill_id:
        raise HTTPException(404, "Attachment not found")
    return await proxy_download(att.storage_key, token)


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    drill_id: uuid.UUID, attachment_id: uuid.UUID,
    db: SessionDep, user: CanWriteFirstAid, token: BearerToken,  # noqa: ARG001
):
    # ★ The one that mattered: evidence on a closed drill was deletable.
    crud.assert_not_evaluated(await crud.get_drill(db, drill_id), "its evidence")
    att = await db.get(DrillAttachment, attachment_id)
    if att is None or att.drill_id != drill_id:
        raise HTTPException(404, "Attachment not found")
    await delete_from_file_server(att.storage_key, token)
    await db.delete(att)
