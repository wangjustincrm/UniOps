"""Expense claim attachment endpoints — files stored on file-api (:8005)."""
import uuid
from datetime import datetime

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import BearerTokenDep, CurrentUserDep, SessionDep
from app.crud.expense import get_by_id as get_claim
from app.models.expense import ExpenseAttachment
from app.services.attachment_helper import (
    delete_from_file_server, proxy_download, upload_to_file_server,
)

router = APIRouter(prefix="/expenses/{claim_id}/attachments", tags=["expense-attachments"])

MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    claim_id: uuid.UUID
    file_id: str          # UUID string — file-api storage key
    file_name: str
    file_size_bytes: int
    mime_type: str | None
    uploaded_at: datetime
    download_url: str | None = None


def _out(att: ExpenseAttachment) -> AttachmentOut:
    dl = f"/api/v1/expenses/{att.claim_id}/attachments/{att.id}/file" if att.file_id else None
    return AttachmentOut(
        id=att.id, claim_id=att.claim_id, file_id=att.file_id,
        file_name=att.file_name, file_size_bytes=att.file_size_bytes,
        mime_type=att.mime_type, uploaded_at=att.uploaded_at, download_url=dl,
    )


@router.get("", response_model=list[AttachmentOut])
async def list_claim_attachments(claim_id: uuid.UUID, db: SessionDep, _: CurrentUserDep):
    result = await db.execute(
        select(ExpenseAttachment)
        .where(ExpenseAttachment.claim_id == claim_id)
        .order_by(ExpenseAttachment.uploaded_at)
    )
    return [_out(a) for a in result.scalars()]


@router.post("", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_claim_attachment(
    claim_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
    file: UploadFile = File(...),
):
    """Upload a receipt / supporting document for an expense claim to file-api."""
    claim = await get_claim(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Expense claim not found")
    if claim.status not in ("draft", "returned"):
        raise HTTPException(status_code=409, detail="Cannot add attachments to a submitted claim")

    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB)")

    # Map claim type to a doc_type tag for file-api audit trail
    claim_type_lower = claim.claim_type.lower().split("_")[0]  # "exp", "mil", "trv", "cfm"

    try:
        storage_key = await upload_to_file_server(
            data,
            filename=file.filename or "attachment",
            content_type=file.content_type or "application/octet-stream",
            doc_type=claim_type_lower,
            doc_id=claim_id,
            bearer_token=token,
            service="oa",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    att = ExpenseAttachment(
        claim_id=claim_id,
        file_id=str(storage_key),
        file_name=file.filename or "attachment",
        file_size_bytes=len(data),
        mime_type=file.content_type,
    )
    db.add(att)
    await db.flush()
    return _out(att)


@router.get("/{att_id}/file")
async def download_claim_attachment(
    claim_id: uuid.UUID,
    att_id: uuid.UUID,
    db: SessionDep,
    _: CurrentUserDep,
    token: BearerTokenDep,
):
    """Proxy file download from file-api."""
    att = await db.get(ExpenseAttachment, att_id)
    if not att or att.claim_id != claim_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if not att.file_id:
        raise HTTPException(status_code=410, detail="File reference missing")
    return await proxy_download(uuid.UUID(att.file_id), token)


@router.delete("/{att_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_claim_attachment(
    claim_id: uuid.UUID,
    att_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
):
    att = await db.get(ExpenseAttachment, att_id)
    if not att or att.claim_id != claim_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    claim = await get_claim(db, claim_id)
    if claim and claim.status not in ("draft", "returned"):
        raise HTTPException(status_code=409, detail="Cannot delete attachment from submitted claim")
    if att.file_id:
        await delete_from_file_server(uuid.UUID(att.file_id), token)
    await db.delete(att)
    await db.flush()
