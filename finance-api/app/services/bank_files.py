"""Keeping the source documents — statements, payment files, the signed report.

finance-api had no file storage at all before Bank Reconciliation v2. This is the
same contract epms-api's attachment_helper uses against file-api, with
service="finance".

Retention is the point: a reconciliation handed to an auditor is only as good as
the documents behind it, and "the statement said so" has to be checkable a year
later. But an unreachable file server must not block a month-end close either —
the numbers are what reconcile, the PDFs are evidence for them. So an upload
failure is reported and recorded, never raised: the row keeps a null storage key
and the caller tells the user the document was not retained.
"""
import logging
import uuid

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

DOC_STATEMENT = "bank_statement"
DOC_ADVICE = "bank_advice"
DOC_RECON = "bank_recon"


async def store(data: bytes, filename: str, content_type: str, doc_type: str,
                doc_id: uuid.UUID, bearer_token: str) -> tuple[uuid.UUID | None, str | None]:
    """-> (storage_key, warning). Never raises."""
    if not settings.file_server_url:
        return None, ("No file server is configured, so the source document was not "
                      "retained. The figures below are unaffected.")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.file_server_url}/files",
                params={"doc_type": doc_type, "doc_id": str(doc_id), "service": "finance"},
                files={"file": (filename, data, content_type)},
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
        if not resp.is_success:
            raise RuntimeError(f"{resp.status_code}: {resp.text[:200]}")
        return uuid.UUID(resp.json()["id"]), None
    except Exception as exc:  # noqa: BLE001 — retention must not block a close
        log.warning("bank document %s (%s) was not retained: %s", filename, doc_type, exc)
        return None, (f"{filename} was read, but could not be saved to the file server "
                      f"({exc}). The figures are unaffected; re-upload it later so the "
                      f"reconciliation keeps its evidence.")


async def fetch(storage_key: uuid.UUID, bearer_token: str) -> httpx.Response:
    """Stream one stored document back. Raises — a download that silently returns
    nothing is worse than an error."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{settings.file_server_url}/files/{storage_key}",
                                headers={"Authorization": f"Bearer {bearer_token}"})
    if not resp.is_success:
        raise RuntimeError(f"File server returned {resp.status_code} for {storage_key}")
    return resp
