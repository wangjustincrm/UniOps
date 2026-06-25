"""Load staged INVOICE attachments into EPMS.

Files are uploaded to the file server (file-api) and recorded in the
``invoice_attachments`` table (owned by expense-api) with invoice_source='epms',
mirroring expense-api's own upload flow. Maps SharePoint invoice id → EPMS invoice
via invoices.internal_ref = 'INV-<spid>'.

Writes to the file server, so it only runs on a committed (non-dry-run) load.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import create_access_token

DATA_DIR = Path(__file__).resolve().parent / "data"
ATT_DIR = DATA_DIR / "invoice_attachments"
SYSTEM_USER_EMAIL = "migration@epms.local"


async def _upload_to_file_server(data: bytes, filename: str, content_type: str,
                                 doc_id, bearer_token: str) -> uuid.UUID:
    """Upload bytes to the file server (service='oa', matching expense-api)."""
    url = f"{settings.FILE_SERVER_URL}/files"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            params={"doc_type": "invoice", "doc_id": str(doc_id), "service": "oa"},
            files={"file": (filename, data, content_type)},
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
    if not resp.is_success:
        raise RuntimeError(f"file server {resp.status_code}: {resp.text[:120]}")
    return uuid.UUID(resp.json()["id"])


@dataclass
class AttachReport:
    uploaded: int = 0
    skipped_existing: int = 0
    no_invoice: int = 0
    failed: int = 0
    samples_failed: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "uploaded": self.uploaded,
            "skipped_existing": self.skipped_existing,
            "no_invoice": self.no_invoice,
            "failed": self.failed,
            "samples_failed": self.samples_failed[:15],
        }


async def sync_invoice_attachments(dry_run: bool = True, db_url: str | None = None) -> AttachReport:
    rep = AttachReport()
    meta_file = DATA_DIR / "invoice_attachments.json"
    if not meta_file.exists():
        return rep
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    if not meta:
        return rep

    engine = create_async_engine(db_url or settings.DATABASE_URL, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            # sp invoice id → EPMS invoice uuid (internal_ref = 'INV-<spid>')
            rows = (await db.execute(text(
                "select internal_ref, id from invoices where internal_ref like 'INV-%'"
            ))).all()
            inv_by_ref = {r[0]: r[1] for r in rows}
            sysid = (await db.execute(text(
                "select id from users where email=:e"), {"e": SYSTEM_USER_EMAIL})).scalar()

            # existing epms attachments per invoice (idempotency by invoice_id+file_name)
            existing = set()
            for iid, fn in (await db.execute(text(
                "select invoice_id, file_name from invoice_attachments where invoice_source='epms'"
            ))).all():
                existing.add((iid, fn))

            token = None if dry_run else create_access_token(subject=str(sysid), role="system_admin")

            for m in meta:
                inv_id = inv_by_ref.get(f"INV-{m['sp_invoice_id']}")
                if not inv_id:
                    rep.no_invoice += 1
                    continue
                if (inv_id, m["file_name"]) in existing:
                    rep.skipped_existing += 1
                    continue
                if dry_run:
                    rep.uploaded += 1  # would upload
                    continue
                fpath = ATT_DIR / str(m["sp_invoice_id"]) / m["file_name"]
                if not fpath.exists():
                    rep.failed += 1
                    rep.samples_failed.append(f"missing file {fpath.name}")
                    continue
                try:
                    data = fpath.read_bytes()
                    storage_key = await _upload_to_file_server(
                        data, m["file_name"], m["content_type"], inv_id, token,
                    )
                    await db.execute(text(
                        "insert into invoice_attachments "
                        "(id, invoice_id, invoice_source, file_name, content_type, "
                        " file_size_bytes, storage_key, uploaded_by, uploaded_at) "
                        "values (:id,:iid,'epms',:fn,:ct,:sz,:sk,:ub,:ts)"
                    ), {
                        "id": uuid.uuid4(), "iid": inv_id, "fn": m["file_name"],
                        "ct": m["content_type"], "sz": m["size"], "sk": storage_key,
                        "ub": sysid, "ts": datetime.now(timezone.utc),
                    })
                    existing.add((inv_id, m["file_name"]))
                    rep.uploaded += 1
                except Exception as e:  # noqa: BLE001
                    rep.failed += 1
                    rep.samples_failed.append(f"{m['file_name']}: {type(e).__name__}")
            if not dry_run:
                await db.commit()
    finally:
        await engine.dispose()
    return rep
