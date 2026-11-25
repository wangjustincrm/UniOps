"""A person's files on an order or a transfer (user 2026-11-13): list, attach, remove (unlink, with a reason).
The service checks the permissions (the record's read / change)."""
from typing import Annotated, Any

from fastapi import Depends, File, Form, UploadFile

from app.core.deps import get_uow
from app.platform.files import read_upload
from app.platform.routes import Router
from app.schemas.records import AttachmentRead, RemoveAttachmentIn
from app.services.record_files import Attachments, kind_of

UoW = Annotated[Any, Depends(get_uow)]

router = Router(prefix="/attachments", tags=["attachments"])


@router.get("/{entity_type}/{entity_id}", response_model=list[AttachmentRead])
async def list_attachments(u: UoW, entity_type: str, entity_id: int):
    return await Attachments(u).list(entity_type, entity_id)


@router.post("/{entity_type}/{entity_id}", response_model=AttachmentRead, status_code=201)
async def attach(u: UoW, entity_type: str, entity_id: int, file: Annotated[UploadFile, File()],
                 note: Annotated[str | None, Form()] = None):
    data = await read_upload(file, kind_of(entity_type).max_bytes)
    return await Attachments(u).attach(entity_type, entity_id, name=file.filename or "file", data=data, note=note)


@router.post("/{entity_type}/{entity_id}/{document_id}/remove", status_code=204)
async def remove(u: UoW, entity_type: str, entity_id: int, document_id: int, body: RemoveAttachmentIn) -> None:
    await Attachments(u).remove(entity_type, entity_id, document_id, body.reason)
