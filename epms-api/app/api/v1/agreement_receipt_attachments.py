"""Agreement receipt attachment endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app.core.authz import require_permission
from app.core.config import settings
from app.core.deps import BearerToken, SessionDep
from app.models.agreement_receipt import AgreementReceipt
from app.models.agreement_receipt_attachment import AgreementReceiptAttachment
from app.services.attachment_helper import delete_from_file_server, proxy_download, upload_to_file_server

router = APIRouter(
    prefix="/agreements/{agreement_id}/receipts/{receipt_id}/attachments", tags=["agreement-receipt-attachments"]
)

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

ReceiptAttReadDep = Annotated[dict, Depends(require_permission("epms.agreement.read"))]
# Same key as agreement_receipts.py's ReceiptRecordDep, not epms.agreement.write:
# a receipt's photo/proof attachments are part of recording the receipt itself
# (ReceiptEntryForm uploads them as step 2 of a single create-then-attach flow),
# not part of editing the agreement's own terms. Someone who can record a
# receipt but can't attach its photo would be a dead end. The permission key
# itself is untouched here (Task 4 renames it).
ReceiptAttWriteDep = Annotated[dict, Depends(require_permission("epms.agreement.slip.write"))]


class AttachmentMeta(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    created_at: str
    download_url: str | None = None

    model_config = {"from_attributes": True}


def _meta(att: AgreementReceiptAttachment) -> AttachmentMeta:
    dl_url = f"{settings.FILE_SERVER_URL}/files/{att.storage_key}" if att.storage_key else None
    return AttachmentMeta(
        id=att.id, filename=att.filename,
        content_type=att.content_type, file_size=att.file_size,
        created_at=att.created_at.isoformat(), download_url=dl_url,
    )


async def _get_receipt_or_404(
    db: SessionDep, agreement_id: uuid.UUID, receipt_id: uuid.UUID,
) -> AgreementReceipt:
    receipt = (await db.execute(
        select(AgreementReceipt).where(
            AgreementReceipt.id == receipt_id,
            AgreementReceipt.agreement_id == agreement_id,
        )
    )).scalar_one_or_none()
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return receipt


@router.get("", response_model=list[AttachmentMeta])
async def list_attachments(
    agreement_id: uuid.UUID, receipt_id: uuid.UUID, db: SessionDep, _: ReceiptAttReadDep,
):
    await _get_receipt_or_404(db, agreement_id, receipt_id)
    result = await db.execute(
        select(AgreementReceiptAttachment)
        .where(AgreementReceiptAttachment.receipt_id == receipt_id)
        .order_by(AgreementReceiptAttachment.created_at)
    )
    return [_meta(r) for r in result.scalars().all()]


@router.post("", response_model=AttachmentMeta, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    agreement_id: uuid.UUID, receipt_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: ReceiptAttWriteDep, token: BearerToken,
):
    await _get_receipt_or_404(db, agreement_id, receipt_id)
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")

    storage_key = await upload_to_file_server(
        data, file.filename or "attachment",
        file.content_type or "application/octet-stream",
        "agreement_receipt", receipt_id, token,
    )
    att = AgreementReceiptAttachment(
        receipt_id=receipt_id,
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
    agreement_id: uuid.UUID, receipt_id: uuid.UUID, att_id: uuid.UUID,
    db: SessionDep, _: ReceiptAttReadDep, token: BearerToken,
):
    await _get_receipt_or_404(db, agreement_id, receipt_id)
    result = await db.execute(
        select(AgreementReceiptAttachment).where(
            AgreementReceiptAttachment.id == att_id, AgreementReceiptAttachment.receipt_id == receipt_id
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
    agreement_id: uuid.UUID, receipt_id: uuid.UUID, att_id: uuid.UUID,
    db: SessionDep, _: ReceiptAttWriteDep, token: BearerToken,
):
    await _get_receipt_or_404(db, agreement_id, receipt_id)
    result = await db.execute(
        select(AgreementReceiptAttachment).where(
            AgreementReceiptAttachment.id == att_id, AgreementReceiptAttachment.receipt_id == receipt_id
        )
    )
    att = result.scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if att.storage_key:
        await delete_from_file_server(att.storage_key, token)
    await db.delete(att)
    await db.flush()
