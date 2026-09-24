"""Upload, stream and delete attachment bytes via file-api.

Deliberately a copy of epms-api's `attachment_helper` rather than a shared
package: the two differ only in the `service` tag they send, and pulling this
into `packages/` would make every service that stores a file depend on a new
shared release. If a third service needs it, that is the moment to extract it.
"""
import logging
import uuid

import httpx
from fastapi import HTTPException
from fastapi.responses import Response

from app.core.config import settings

# Photos arrive already compressed by the browser (long edge 1920, q0.8), which
# puts a phone photo near 400 KB. The ceiling is for the occasional PDF and for
# a client that skipped compression, not for raw camera output.
MAX_FILE_SIZE = 25 * 1024 * 1024


logger = logging.getLogger(__name__)


async def upload_to_file_server(
    data: bytes,
    filename: str,
    content_type: str,
    doc_type: str,
    doc_id: uuid.UUID,
    bearer_token: str,
) -> uuid.UUID:
    """Upload bytes to file-api. Returns the storage key."""
    url = f"{settings.FILE_SERVER_URL}/files"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            params={"doc_type": doc_type, "doc_id": str(doc_id), "service": "ehs"},
            files={"file": (filename, data, content_type)},
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
    if not resp.is_success:
        raise HTTPException(
            status_code=502,
            detail=f"File server error {resp.status_code}: {resp.text[:200]}",
        )
    return uuid.UUID(resp.json()["id"])


async def proxy_download(storage_key: uuid.UUID, bearer_token: str) -> Response:
    """Stream a file back from file-api.

    Proxied rather than linked directly so the browser sends this service's
    bearer token, and so a deleted incident's files stop being reachable.
    """
    url = f"{settings.FILE_SERVER_URL}/files/{storage_key}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {bearer_token}"})
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="File not found on storage server")
    if not resp.is_success:
        raise HTTPException(status_code=502, detail="File server error")
    disposition = resp.headers.get("content-disposition", "")
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers={"Content-Disposition": disposition} if disposition else {},
    )


async def fetch_file_bytes(storage_key: uuid.UUID, bearer_token: str) -> bytes | None:
    """The file's contents, for composing rather than serving.

    Returns None instead of raising: the one caller is the label renderer, and
    a pictogram that cannot be fetched must not take the whole print job down.
    The label draws an empty slot in its place, which is visible.
    """
    url = f"{settings.FILE_SERVER_URL}/files/{storage_key}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {bearer_token}"})
    except httpx.HTTPError:
        logger.warning("pictogram %s could not be fetched from file-api", storage_key)
        return None
    if not resp.is_success:
        logger.warning("pictogram %s: file-api answered %s", storage_key, resp.status_code)
        return None
    return resp.content


async def delete_from_file_server(storage_key: uuid.UUID, bearer_token: str) -> None:
    """Remove the bytes. Uploading handles the file server being unreachable
    and this did not — a network error propagated as an unhandled exception,
    so a delete during a file-api outage answered 500 with a stack trace in
    the log rather than saying what was wrong.

    A failure here is reported rather than swallowed: the caller has usually
    not yet removed its own row, and telling somebody an attachment is gone
    while the file is still on disk is the wrong half to get wrong.
    """
    url = f"{settings.FILE_SERVER_URL}/files/{storage_key}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                url, headers={"Authorization": f"Bearer {bearer_token}"})
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="The file service is not reachable, so the attachment could "
                   "not be removed. Nothing was changed.",
        ) from exc

    # A 404 means the bytes are already gone, which is the state being asked
    # for — the row can go. Anything else is a refusal, and every caller
    # deletes its own row next: swallowing it would report the attachment as
    # removed while the file is still on disk.
    if resp.status_code == 404:
        return
    if not resp.is_success:
        raise HTTPException(
            status_code=502,
            detail=f"The file service refused to remove the file "
                   f"({resp.status_code}). The attachment was left in place.",
        )
