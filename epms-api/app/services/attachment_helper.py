"""Shared helpers for attachment upload/download via file server."""
import uuid
import httpx
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from app.core.config import settings


async def upload_to_file_server(
    data: bytes,
    filename: str,
    content_type: str,
    doc_type: str,
    doc_id: uuid.UUID,
    bearer_token: str,
) -> uuid.UUID:
    """Upload bytes to file server. Returns storage_key UUID."""
    url = f"{settings.FILE_SERVER_URL}/files"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            params={"doc_type": doc_type, "doc_id": str(doc_id), "service": "epms"},
            files={"file": (filename, data, content_type)},
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
    if not resp.is_success:
        raise RuntimeError(f"File server error {resp.status_code}: {resp.text[:200]}")
    return uuid.UUID(resp.json()["id"])


async def proxy_download(storage_key: uuid.UUID, bearer_token: str) -> StreamingResponse:
    """Stream file from file server back to client."""
    url = f"{settings.FILE_SERVER_URL}/files/{storage_key}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {bearer_token}"})
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="File not found on storage server")
    if not resp.is_success:
        raise HTTPException(status_code=502, detail="File server error")
    content_type = resp.headers.get("content-type", "application/octet-stream")
    disposition = resp.headers.get("content-disposition", "")
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Content-Disposition": disposition} if disposition else {},
    )


async def delete_from_file_server(storage_key: uuid.UUID, bearer_token: str) -> None:
    url = f"{settings.FILE_SERVER_URL}/files/{storage_key}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.delete(url, headers={"Authorization": f"Bearer {bearer_token}"})
