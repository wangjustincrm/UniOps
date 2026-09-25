"""Invoice attachment endpoints — files stored on file-api (:8005)."""
import uuid
from datetime import datetime

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz_matrix import has_permission, user_role_codes
from app.core.deps import BearerTokenDep, CurrentUserDep, SessionDep
from app.models.invoice_attachment import InvoiceAttachment
from app.services.attachment_helper import (
    delete_from_file_server, proxy_download, upload_to_file_server,
)

router = APIRouter(prefix="/invoice-attachments", tags=["invoice-attachments"])

MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB

# Browsers send no usable type for saved emails: Chrome on a PC without Outlook
# registered reports "" for .msg, which reaches us as application/octet-stream
# and would be refused by file-api's allowlist. Only these two are re-typed from
# the extension — anything else keeps what the browser said.
_EMAIL_TYPES = {".msg": "application/vnd.ms-outlook", ".eml": "message/rfc822"}


def _content_type(file: UploadFile) -> str:
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype in ("", "application/octet-stream"):
        name = (file.filename or "").lower()
        for ext, email_type in _EMAIL_TYPES.items():
            if name.endswith(ext):
                return email_type
    return file.content_type or "application/octet-stream"


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


async def _role_grants_read(
    db: AsyncSession, user_id: uuid.UUID, role: str, invoice_source: str
) -> bool:
    """Whether the caller's ROLES (independent of who uploaded the file) may
    read attachments of an invoice from `invoice_source`.

    `invoice_attachments` is shared, and the two sources have genuinely
    different access models — collapsing them onto "did you upload it" (the
    original IDOR fix, b345e09) is what emptied the EPMS Invoice Detail
    Attachments panel for everyone outside AP/finance: in production 7272 of
    7275 rows are EPMS invoices, almost all uploaded by AP or the PMS import.

      epms → a procurement document. Gated on the same Access Control key
             EPMS's own invoice endpoints use, `view_invoice`. Per-document
             scope stays with epms-api, which is the authority deciding
             whether the caller can open the invoice at all; this service has
             no equivalent of its access_scope and a second, drifting copy
             would hide files from people who can see the invoice itself.
      oa   → a personal reimbursement receipt. Stays owner-only, with payer
             roles (AP/finance) able to review — that privacy model was the
             point of the audit fix and is unchanged here.

    Both branches now resolve the caller's FULL role set (primary ∪
    user_roles). The old check compared the JWT's primary role against
    _CAN_PAY, so an ap_clerk holding that as an ADDITIONAL role failed it —
    the same class of bug as the dept_admin role-code drift.
    """
    from app.api.v1.expenses import _CAN_PAY
    if role == "system_admin":
        return True
    if invoice_source == "epms":
        return await has_permission(db, user_id, role, "view_invoice")
    return bool(await user_role_codes(db, user_id, role) & _CAN_PAY)


async def _can_delete_invoice_attachment(
    db: AsyncSession, att: InvoiceAttachment, user_id: uuid.UUID, role: str
) -> bool:
    """Deletion stays as tight as the audit fix left it — uploader or a payer
    role. Being allowed to READ an invoice's file must not confer the right to
    remove it, so this deliberately does NOT consult `view_invoice`.
    """
    from app.api.v1.expenses import _CAN_PAY
    if role == "system_admin" or att.uploaded_by == user_id:
        return True
    return bool(await user_role_codes(db, user_id, role) & _CAN_PAY)


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
    invoice_source: str = Query("oa"),   # 'epms' | 'oa' | 'credit'
    file: UploadFile = File(...),
):
    """Upload an invoice file to file-api and record metadata in invoice_attachments."""
    # 'credit' = a finance-api vendor_credits row; invoice_id then carries the
    # vendor credit's id. api/v1/invoice_list.py filters on the literal 'epms' /
    # 'oa' values, so credits never leak into the unified invoice list.
    if invoice_source not in ("epms", "oa", "credit"):
        raise HTTPException(status_code=400,
                            detail="invoice_source must be 'epms', 'oa' or 'credit'")

    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB)")
    content_type = _content_type(file)

    # Upload to file-api
    try:
        storage_key = await upload_to_file_server(
            data,
            filename=file.filename or "invoice",
            content_type=content_type,
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
        content_type=content_type,
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
    user: CurrentUserDep,
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
    uid = uuid.UUID(user["sub"])
    role = user.get("role", "")
    # One matrix read for the whole page — the role verdict is the same for
    # every row, only `uploaded_by` varies.
    role_may_read = await _role_grants_read(db, uid, role, invoice_source)
    return [_meta(a) for a in result.scalars() if role_may_read or a.uploaded_by == uid]


@router.get("/{attachment_id}/file")
async def serve_attachment(
    attachment_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
):
    """Proxy file download from file-api."""
    att = await db.get(InvoiceAttachment, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    uid = uuid.UUID(user["sub"])
    role = user.get("role", "")
    if att.uploaded_by != uid and not await _role_grants_read(db, uid, role, att.invoice_source):
        raise HTTPException(status_code=403, detail="Not authorized to access this attachment")
    if not att.storage_key:
        raise HTTPException(status_code=410, detail="File not available (legacy record without storage key)")
    return await proxy_download(att.storage_key, token)


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
):
    att = await db.get(InvoiceAttachment, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if not await _can_delete_invoice_attachment(db, att, uuid.UUID(user["sub"]), user.get("role", "")):
        raise HTTPException(status_code=403, detail="Not authorized to access this attachment")
    if att.storage_key:
        await delete_from_file_server(att.storage_key, token)
    await db.delete(att)
    await db.flush()
