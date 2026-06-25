"""File upload / download / delete endpoints."""
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.core.config import settings
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.file_meta import FileMetadata
from app.schemas.file import FileMetaResponse

router = APIRouter(prefix="/files", tags=["files"])


def _storage_path(file_id: uuid.UUID, original_filename: str) -> Path:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    suffix = Path(original_filename).suffix or ""
    rel = Path(str(now.year)) / f"{now.month:02d}" / f"{file_id}{suffix}"
    return rel


def _download_url(request: Request, file_id: uuid.UUID) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/files/v1/files/{file_id}"


@router.post("", response_model=FileMetaResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile,
    doc_type: str = Query(..., description="pr | po | pa | gr"),
    doc_id: uuid.UUID = Query(...),
    service: str = Query(default="epms"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = ...,
):
    data = await file.read()
    if len(data) > settings.MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_FILE_SIZE // 1024 // 1024} MB limit")
    # content-type allowlist (documents + images) — reject executables/scripts/etc.
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in settings.ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{file.content_type}'. Allowed: PDF, images, Office docs, CSV/text.",
        )

    file_id = uuid.uuid4()
    rel_path = _storage_path(file_id, file.filename or "file")
    abs_path = settings.STORAGE_ROOT / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(abs_path, "wb") as f:
        await f.write(data)

    meta = FileMetadata(
        id=file_id,
        original_filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        file_size=len(data),
        storage_path=str(rel_path).replace("\\", "/"),
        uploaded_by=uuid.UUID(user["sub"]),
        service=service,
        doc_type=doc_type,
        doc_id=doc_id,
    )
    db.add(meta)
    await db.flush()
    await db.refresh(meta)

    return FileMetaResponse(
        id=meta.id,
        original_filename=meta.original_filename,
        content_type=meta.content_type,
        file_size=meta.file_size,
        service=meta.service,
        doc_type=meta.doc_type,
        doc_id=meta.doc_id,
        created_at=meta.created_at.isoformat(),
        download_url=_download_url(request, meta.id),
    )


@router.get("/{file_id}/meta", response_model=FileMetaResponse)
async def get_file_meta(
    request: Request,
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    result = await db.execute(select(FileMetadata).where(FileMetadata.id == file_id, FileMetadata.is_deleted.is_(False)))
    meta = result.scalar_one_or_none()
    if meta is None:
        raise HTTPException(status_code=404, detail="File not found")
    return FileMetaResponse(
        id=meta.id,
        original_filename=meta.original_filename,
        content_type=meta.content_type,
        file_size=meta.file_size,
        service=meta.service,
        doc_type=meta.doc_type,
        doc_id=meta.doc_id,
        created_at=meta.created_at.isoformat(),
        download_url=_download_url(request, meta.id),
    )


@router.get("/{file_id}")
async def download_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    result = await db.execute(select(FileMetadata).where(FileMetadata.id == file_id, FileMetadata.is_deleted.is_(False)))
    meta = result.scalar_one_or_none()
    if meta is None:
        raise HTTPException(status_code=404, detail="File not found")

    abs_path = settings.STORAGE_ROOT / meta.storage_path
    if not abs_path.exists():
        raise HTTPException(status_code=410, detail="File data not found on disk")

    return FileResponse(
        path=str(abs_path),
        media_type=meta.content_type,
        filename=meta.original_filename,
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    result = await db.execute(select(FileMetadata).where(FileMetadata.id == file_id))
    meta = result.scalar_one_or_none()
    if meta is None:
        raise HTTPException(status_code=404, detail="File not found")
    meta.is_deleted = True
    # Optionally remove from disk
    abs_path = settings.STORAGE_ROOT / meta.storage_path
    if abs_path.exists():
        abs_path.unlink(missing_ok=True)
    await db.flush()
