"""PO attachment endpoints."""
import asyncio
import uuid

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app.core.access_scope import role_holder_ids
from app.core.config import settings
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud.po import get_by_id as get_po
from app.models.config import CompanyConfig
from app.models.po_attachment import PoAttachment
from app.models.user import User
from app.services.attachment_helper import delete_from_file_server, proxy_download, upload_to_file_server
from app.services.pdf_po import generate_po_pdf

router = APIRouter(prefix="/po/{po_id}/attachments", tags=["po-attachments"])

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

# Statuses at/after which the PO PDF is meaningful (regenerate-able).
#
# ``nc_pending`` — an NC order still working through the ERP's approval chain —
# is included deliberately, and is the one entry that is not "at/after approval":
# the printed PDF is what gets signed off-line, and those signatures are what
# feed NC's approval. Requiring approval first would make the document
# unobtainable exactly when it is needed. The PO stays read-only in every other
# respect; nothing about holding a PDF makes it payable or receivable.
#
# ``nc_milk`` — an NC order of a non raw-material trade type — is here for the
# same reason ``issued`` is: NC approved it, and it is the document the sign-off
# stamps its signatures onto. Nothing else regenerates a PDF for an imported PO
# (po_action never runs on one), so without this entry a signed milk order has
# no page to carry the signatures and no one can print it.
_PDF_STATUSES = {"approved", "issued", "partially_received", "fully_received",
                 "closed", "nc_pending", "nc_milk"}


class AttachmentMeta(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    created_at: str
    download_url: str | None = None

    model_config = {"from_attributes": True}


def _meta(att: PoAttachment) -> AttachmentMeta:
    dl_url = f"{settings.FILE_SERVER_URL}/files/{att.storage_key}" if att.storage_key else None
    return AttachmentMeta(
        id=att.id, filename=att.filename,
        content_type=att.content_type, file_size=att.file_size,
        created_at=att.created_at.isoformat(), download_url=dl_url,
    )


@router.get("", response_model=list[AttachmentMeta])
async def list_attachments(po_id: uuid.UUID, db: SessionDep, _: CurrentUserPayload):
    result = await db.execute(
        select(PoAttachment).where(PoAttachment.po_id == po_id).order_by(PoAttachment.created_at)
    )
    return [_meta(r) for r in result.scalars().all()]


@router.post("", response_model=AttachmentMeta, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    po_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CurrentUserPayload, token: BearerToken,
):
    po = await get_po(db, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO not found")
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")

    storage_key = await upload_to_file_server(
        data, file.filename or "attachment",
        file.content_type or "application/octet-stream",
        "po", po_id, token,
    )
    att = PoAttachment(
        po_id=po_id,
        filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        file_size=len(data),
        storage_key=storage_key,
    )
    db.add(att)
    await db.flush()
    await db.refresh(att)
    return _meta(att)


@router.post("/regenerate-pdf", response_model=AttachmentMeta)
async def regenerate_pdf(
    po_id: uuid.UUID,
    db: SessionDep, user: CurrentUserPayload, token: BearerToken,
):
    """(Re)generate the approved-PO PDF and (re)attach it.

    Backfills the PDF on POs that reached ``approved`` without passing through
    the live approval action (e.g. PMS-imported POs), or refreshes it after a
    template/logo change. Replaces any existing ``<number>.pdf`` attachment.
    """
    po = await get_po(db, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO not found")
    if po.status not in _PDF_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"PDF is not available for a PO in status '{po.status}'")

    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    company_name = cfg.name if cfg else "EPMS"

    # Type 1 POs carry a signature block naming the OPM as our company's
    # signatory. role_holder_ids counts a PRIMARY (users.role) or ADDITIONAL
    # (identity's user_roles) "opm" role, active users only. Leave the name
    # blank unless exactly one holder is found — never print an
    # arbitrarily-chosen name on a vendor-facing document.
    signatory_name = None
    if po.type == 1:
        opm_holders = (await role_holder_ids(db, codes=("opm",))).get("opm", set())
        if len(opm_holders) == 1:
            signatory_name = (await db.execute(
                select(User.full_name).where(User.id == next(iter(opm_holders)))
            )).scalar_one_or_none()

    signatures = [
        {"sig_slot": sig.sig_slot, "signer_name": sig.signer_name,
         "signature_image": sig.signature_image}
        for sig in sorted(po.signoff_signatures, key=lambda s: s.step_idx)
    ]

    filename = f"{po.number}.pdf"
    loop = asyncio.get_event_loop()
    pdf_bytes = await loop.run_in_executor(
        None, generate_po_pdf, po, company_name,
        cfg.pdf_templates if cfg else None,
        cfg.logo_data_url if cfg else None,
        signatory_name,
        signatures,
    )

    existing = (await db.execute(
        select(PoAttachment).where(
            PoAttachment.po_id == po_id,
            PoAttachment.filename == filename,
        )
    )).scalars().all()
    for att in existing:
        if att.storage_key:
            await delete_from_file_server(att.storage_key, token)
        await db.delete(att)
    await db.flush()

    storage_key = await upload_to_file_server(
        pdf_bytes, filename, "application/pdf", "po", po_id, token,
    )
    att = PoAttachment(
        po_id=po_id, filename=filename,
        content_type="application/pdf", file_size=len(pdf_bytes),
        storage_key=storage_key,
    )
    db.add(att)
    await db.flush()
    await db.refresh(att)
    return _meta(att)


@router.get("/{att_id}/download")
async def download_attachment(
    po_id: uuid.UUID, att_id: uuid.UUID,
    db: SessionDep, _: CurrentUserPayload, token: BearerToken,
):
    result = await db.execute(
        select(PoAttachment).where(PoAttachment.id == att_id, PoAttachment.po_id == po_id)
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
    po_id: uuid.UUID, att_id: uuid.UUID,
    db: SessionDep, _: CurrentUserPayload, token: BearerToken,
):
    result = await db.execute(
        select(PoAttachment).where(PoAttachment.id == att_id, PoAttachment.po_id == po_id)
    )
    att = result.scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if att.storage_key:
        await delete_from_file_server(att.storage_key, token)
    await db.delete(att)
    await db.flush()
