"""HTTP client — EPMS → File Server."""
import uuid
import httpx
from app.core.config import settings


async def upload_bytes(
    data: bytes,
    filename: str,
    content_type: str,
    doc_type: str,
    doc_id: uuid.UUID,
    bearer_token: str,
) -> uuid.UUID:
    """Upload bytes to file server. Returns the storage_key (file UUID)."""
    url = f"{settings.FILE_SERVER_URL}/files"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            params={"doc_type": doc_type, "doc_id": str(doc_id), "service": "epms"},
            files={"file": (filename, data, content_type)},
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
    if not resp.is_success:
        raise RuntimeError(f"File server upload failed {resp.status_code}: {resp.text[:200]}")
    return uuid.UUID(resp.json()["id"])


def download_url(storage_key: uuid.UUID) -> str:
    return f"{settings.FILE_SERVER_URL}/files/{storage_key}"
