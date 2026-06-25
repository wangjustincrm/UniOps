"""Shared helpers for file upload/download via file-api (port 8005)."""
import uuid

import httpx
from fastapi import HTTPException
from fastapi.responses import Response

from app.core.config import settings


async def upload_to_file_server(
    data: bytes,
    filename: str,
    content_type: str,
    doc_type: str,
    doc_id: uuid.UUID,
    bearer_token: str,
    service: str = "oa",
) -> uuid.UUID:
    """Upload bytes to file-api. Returns the file UUID (storage_key)."""
    url = f"{settings.file_server_url}/files"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            params={"doc_type": doc_type, "doc_id": str(doc_id), "service": service},
            files={"file": (filename, data, content_type)},
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
    if not resp.is_success:
        raise RuntimeError(
            f"File server error {resp.status_code}: {resp.text[:200]}. "
            "Is file-api running? Run: ./check-health.sh"
        )
    return uuid.UUID(resp.json()["id"])


async def proxy_download(storage_key: uuid.UUID, bearer_token: str) -> Response:
    """Proxy a file from file-api back to the client."""
    url = f"{settings.file_server_url}/files/{storage_key}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {bearer_token}"})
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="File not found on storage server")
    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"File server error {resp.status_code}")
    content_type = resp.headers.get("content-type", "application/octet-stream")
    disposition = resp.headers.get("content-disposition", "")
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Content-Disposition": disposition} if disposition else {},
    )


async def delete_from_file_server(storage_key: uuid.UUID, bearer_token: str) -> None:
    """Soft-delete a file from file-api (marks is_deleted=True, removes from disk)."""
    url = f"{settings.file_server_url}/files/{storage_key}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.delete(url, headers={"Authorization": f"Bearer {bearer_token}"})
