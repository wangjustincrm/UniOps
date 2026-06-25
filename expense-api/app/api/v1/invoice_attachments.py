"""Invoice attachment endpoints — files stored on file-api (:8005)."""
import uuid
from datetime import datetime

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import BearerTokenDep, CurrentUserDep, SessionDep
from app.models.invoice_attachment import InvoiceAttachment
from app.services.attachment_helper import (
    delete_from_file_server, proxy_download, upload_to_file_server,
)

router = APIRouter(prefix="/invoice-attachments", tags=["invoice-attachments"])

MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB


class AttachmentMeta(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_source: str
    file_name: str
    content_type: str
    file_size_bytes: int
    storage_key: uuid.UUID | None
    uploaded_by: uuid.UUID
    uploaded_at: datetime
    download_url: str | None = None


def _meta(att: InvoiceAttachment, base_url: str = "") -> AttachmentMeta:
    dl = f"{base_url}/api/v1/invoice-attachments/{att.id}/file" if att.storage_key else None
    return AttachmentMeta(
        id=att.id, invoice_id=att.invoice_id, invoice_source=att.invoice_source,
        file_name=att.file_name, content_type=att.content_type,
        file_size_bytes=att.file_size_bytes, storage_key=att.storage_key,
        uploaded_by=att.uploaded_by, uploaded_at=att.uploaded_at, download_url=dl,
    )


@router.post("", response_model=AttachmentMeta, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
    invoice_id: uuid.UUID = Query(...),
    invoice_source: str = Query("oa"),   # 'epms' | 'oa'
    file: UploadFile = File(...),
):
    """Upload an invoice file to file-api and record metadata in invoice_attachments."""
    if invoice_source not in ("epms", "oa"):
        raise HTTPException(status_code=400, detail="invoice_source must be 'epms' or 'oa'")

    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB)")

    # Upload to file-api
    try:
        storage_key = await upload_to_file_server(
            data,
            filename=file.filename or "invoice",
            content_type=file.content_type or "application/octet-stream",
            doc_type="invoice",
            doc_id=invoice_id,
            bearer_token=token,
            service="oa",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    att = InvoiceAttachment(
        invoice_id=invoice_id,
        invoice_source=invoice_source,
        file_name=file.filename or "invoice",
        content_type=file.content_type or "application/octet-stream",
        file_size_bytes=len(data),
        storage_key=storage_key,
        uploaded_by=uuid.UUID(user["sub"]),
    )
    db.add(att)
    await db.flush()
    return _meta(att)


@router.get("", response_model=list[AttachmentMeta])
async def list_attachments(
    db: SessionDep,
    _: CurrentUserDep,
    invoice_id: uuid.UUID = Query(...),
    invoice_source: str = Query("oa"),
):
    result = await db.execute(
        select(InvoiceAttachment)
        .where(
            InvoiceAttachment.invoice_id == invoice_id,
            InvoiceAttachment.invoice_source == invoice_source,
        )
        .order_by(InvoiceAttachment.uploaded_at)
    )
    return [_meta(a) for a in result.scalars()]


@router.get("/{attachment_id}/file")
async def serve_attachment(
    attachment_id: uuid.UUID,
    db: SessionDep,
    _: CurrentUserDep,
    token: BearerTokenDep,
):
    """Proxy file download from file-api."""
    att = await db.get(InvoiceAttachment, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if not att.storage_key:
        raise HTTPException(status_code=410, detail="File not available (legacy record without storage key)")
    return await proxy_download(att.storage_key, token)


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: uuid.UUID,
    db: SessionDep,
    _: CurrentUserDep,
    token: BearerTokenDep,
):
    att = await db.get(InvoiceAttachment, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if att.storage_key:
        await delete_from_file_server(att.storage_key, token)
    await db.delete(att)
    await db.flush()
