"""Agreement attachment endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app.core.access_scope import build_scope, is_agreement_visible
from app.core.authz import require_permission
from app.core.config import settings
from app.core.deps import BearerToken, SessionDep
from app.crud.agreement import get_by_id as get_agreement
from app.models.agreement_attachment import AgreementAttachment
from app.services.attachment_helper import delete_from_file_server, proxy_download, upload_to_file_server

router = APIRouter(prefix="/agreements/{agreement_id}/attachments", tags=["agreement-attachments"])

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

AgrAttReadDep = Annotated[dict, Depends(require_permission("epms.agreement.read"))]
AgrAttWriteDep = Annotated[dict, Depends(require_permission("epms.agreement.write"))]


class AttachmentMeta(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    created_at: str
    download_url: str | None = None

    model_config = {"from_attributes": True}


def _meta(att: AgreementAttachment) -> AttachmentMeta:
    dl_url = f"{settings.FILE_SERVER_URL}/files/{att.storage_key}" if att.storage_key else None
    return AttachmentMeta(
        id=att.id, filename=att.filename,
        content_type=att.content_type, file_size=att.file_size,
        created_at=att.created_at.isoformat(), download_url=dl_url,
    )


@router.get("", response_model=list[AttachmentMeta])
async def list_attachments(agreement_id: uuid.UUID, db: SessionDep, user: AgrAttReadDep):
    if not await is_agreement_visible(db, agreement_id, await build_scope(db, user)):
        raise HTTPException(status_code=404, detail="Agreement not found")
    result = await db.execute(
        select(AgreementAttachment)
        .where(AgreementAttachment.agreement_id == agreement_id)
        .order_by(AgreementAttachment.created_at)
    )
    return [_meta(r) for r in result.scalars().all()]


@router.post("", response_model=AttachmentMeta, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    agreement_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: AgrAttWriteDep, token: BearerToken,
):
    agr = await get_agreement(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if not await is_agreement_visible(db, agreement_id, await build_scope(db, user)):
        raise HTTPException(status_code=404, detail="Agreement not found")
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")

    storage_key = await upload_to_file_server(
        data, file.filename or "attachment",
        file.content_type or "application/octet-stream",
        "agreement", agreement_id, token,
    )
    att = AgreementAttachment(
        agreement_id=agreement_id,
        filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        file_size=len(data),
        storage_key=storage_key,
    )
    db.add(att)
    await db.flush()
    await db.refresh(att)
    return _meta(att)


@router.get("/{att_id}/download")
async def download_attachment(
    agreement_id: uuid.UUID, att_id: uuid.UUID,
    db: SessionDep, user: AgrAttReadDep, token: BearerToken,
):
    if not await is_agreement_visible(db, agreement_id, await build_scope(db, user)):
        raise HTTPException(status_code=404, detail="Agreement not found")
    result = await db.execute(
        select(AgreementAttachment).where(
            AgreementAttachment.id == att_id, AgreementAttachment.agreement_id == agreement_id
        )
    )
    att = result.scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if att.storage_key:
        return await proxy_download(att.storage_key, token)
    if att.file_data:
        return Response(
            content=att.file_data, media_type=att.content_type,
            headers={"Content-Disposition": f'attachment; filename="{att.filename}"'},
        )
    raise HTTPException(status_code=410, detail="File data unavailable")


@router.delete("/{att_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    agreement_id: uuid.UUID, att_id: uuid.UUID,
    db: SessionDep, user: AgrAttWriteDep, token: BearerToken,
):
    if not await is_agreement_visible(db, agreement_id, await build_scope(db, user)):
        raise HTTPException(status_code=404, detail="Agreement not found")
    result = await db.execute(
        select(AgreementAttachment).where(
            AgreementAttachment.id == att_id, AgreementAttachment.agreement_id == agreement_id
        )
    )
    att = result.scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if att.storage_key:
        await delete_from_file_server(att.storage_key, token)
    await db.delete(att)
    await db.flush()
