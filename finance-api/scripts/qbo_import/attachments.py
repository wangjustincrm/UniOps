"""Load QBO Attachable metadata, txn links, and downloaded bytes.

The binary is fetched from the signed TempDownloadUri (no auth, expires — so it
must be pulled during the run). A `downloader` with `.get_bytes(url) -> bytes` is
injected so tests run without network. Links are delete-then-insert per attachment.
"""
import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qbo import QboAttachment, QboAttachmentLink
from scripts.qbo_import.mappers import last_updated


class HttpDownloader:
    def get_bytes(self, url: str) -> bytes:
        r = httpx.get(url, timeout=120.0)
        r.raise_for_status()
        return r.content


async def load_attachments(db: AsyncSession, objects: list[dict], downloader=None) -> dict:
    downloader = downloader or HttpDownloader()
    if not objects:
        return {"inserted": 0, "updated": 0}
    existing = set((await db.execute(select(QboAttachment.qbo_id))).scalars().all())
    ins = updated = 0
    for o in objects:
        qid = o["Id"]
        content = None
        uri = o.get("TempDownloadUri")
        if uri:
            content = downloader.get_bytes(uri)
        row = {
            "qbo_id": qid, "file_name": o.get("FileName"),
            "content_type": o.get("ContentType"), "size": o.get("Size"),
            "content": content, "last_updated_time": last_updated(o), "raw": o,
        }
        stmt = insert(QboAttachment).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["qbo_id"],
            set_={k: stmt.excluded[k] for k in row if k != "qbo_id"})
        await db.execute(stmt)
        if qid in existing:
            updated += 1
        else:
            ins += 1

        await db.execute(delete(QboAttachmentLink).where(QboAttachmentLink.attachment_qbo_id == qid))
        for ref in (o.get("AttachableRef") or []):
            er = ref.get("EntityRef") or {}
            if er.get("value"):
                await db.execute(insert(QboAttachmentLink).values(
                    attachment_qbo_id=qid, txn_id=er["value"], txn_type=er.get("type")))
    await db.commit()
    db.expire_all()
    return {"inserted": ins, "updated": updated}
