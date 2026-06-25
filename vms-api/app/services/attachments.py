"""Visitor attachment proxy (PRD §6.5.6 / W11–12 / S2-E).

vms-api doesn't own file storage — it forwards uploads to file-api (which
manages `file_metadata` + the on-disk store) and reads back the metadata.

  - Upload: stream the multipart bytes to file-api with doc_type="vms_visit".
  - List:   raw-SQL read of `file_metadata` rows for the visit. file-api
            doesn't currently expose a "list by doc_id" endpoint; rather than
            push that endpoint into file-api right now, we read the shared
            table directly (same pattern as notifications.py reads
            company_config for SMTP).
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


async def list_attachments(db: AsyncSession, visit_id: uuid.UUID) -> list[dict[str, Any]]:
    """Read attachments from the file-api-owned `file_metadata` table.

    Returns metadata dicts with a pre-computed download URL pointing at
    file-api. Caller will hit that URL with the same Bearer token.
    """
    rows = (await db.execute(
        text(
            "SELECT id, original_filename, content_type, file_size, "
            "       service, doc_type, doc_id, uploaded_by, created_at "
            "FROM file_metadata "
            "WHERE doc_type = 'vms_visit' AND doc_id = :visit_id "
            "  AND is_deleted = FALSE "
            "ORDER BY created_at DESC"
        ),
        {"visit_id": visit_id},
    )).all()

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id":                str(r[0]),
            "original_filename": r[1],
            "content_type":      r[2],
            "file_size":         r[3],
            "service":           r[4],
            "doc_type":          r[5],
            "doc_id":            str(r[6]),
            "uploaded_by":       str(r[7]) if r[7] else None,
            "created_at":        r[8].isoformat() if r[8] else None,
            "download_url":      f"{settings.FILE_SERVER_URL}/files/{r[0]}",
        })
    return out


async def upload_attachment(
    *,
    visit_id: uuid.UUID,
    bearer_token: str,
    filename: str,
    content_type: str,
    data: bytes,
) -> dict[str, Any]:
    """Forward a single file upload to file-api with `doc_type=vms_visit`.

    Caller validates auth + ownership. file-api enforces size limits and
    persists; we return its metadata response verbatim.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            # FILE_SERVER_URL is `.../files/v1` already.
            f"{settings.FILE_SERVER_URL}/files",
            params={
                "doc_type": "vms_visit",
                "doc_id": str(visit_id),
                "service": "vms",
            },
            headers={"Authorization": f"Bearer {bearer_token}"},
            files={"file": (filename, data, content_type)},
        )
        resp.raise_for_status()
        return resp.json()
