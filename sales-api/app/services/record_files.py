"""What an order and a transfer share (user 2026-11-13: "mostly all features of orders for transfers too,
and attach files"): their files (received, sent and attached), their activity, and a person's
attachments.

An attachment is a document like the EDI files (kind ATTACHMENT, origin USER, sales27): the bytes
kept once (same bytes, same document: attached again it is only linked again), linked to its
record, shown in its Documents tab and viewed in the file viewer. Attaching needs the record's own
change permission. "Remove" takes it off the record (the link, with a reason, in the log and the
audit); the document itself is never deleted (FR-OB-05)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import or_, select

from app.models.documents import Document, DocumentLink
from app.models.orders import Order, OrderLine
from app.models.records import AuditRecord
from app.models.transfers import Transfer, TransferLine
from app.platform.authorization import authorize
from app.platform.errors import BusinessError
from app.schemas.orders import TimelineEntry
from app.schemas.records import AttachmentRead, RecordDocumentRead
from app.services import oplog

MAX_BYTES = 20 * 1024 * 1024
# What a person may attach: documents, pictures, mail, spreadsheets, text (no programs, no archives).
ALLOWED = re.compile(r"\.(pdf|png|jpe?g|gif|webp|heic|tiff?|txt|csv|xlsx?|docx?|pptx?|msg|eml|edi|json|xml)$", re.I)


@dataclass(frozen=True)
class RecordKind:
    entity_type: str        # the DocumentLink entity_type
    model: type
    line_model: type
    line_fk: str
    read: str
    change: str             # the permission to attach / remove
    label: str


RECORDS = {
    "order": RecordKind("order", Order, OrderLine, "order_id", "sales.read", "sales.order.operate", "order"),
    "transfer": RecordKind("transfer", Transfer, TransferLine, "transfer_id", "sales.read", "sales.transfer.manage", "transfer"),
}

_COLUMNS = (Document.id, Document.kind, Document.direction, Document.remote_name, Document.created_at, Document.file_status,
            Document.ack_status, Document.st02, Document.sent_at, Document.error, Document.ack_detail, Document.size,
            Document.created_by_name, Document.body_json)


def kind_of(entity_type: str) -> RecordKind:
    k = RECORDS.get(entity_type)
    if k is None:
        raise BusinessError("record.kind", f"files cannot be attached to {entity_type!r}", status_code=404)
    return k


async def documents_of(s, entity_type: str, entity_id: int) -> list[RecordDocumentRead]:
    """Every file of the record, oldest first: received, sent and attached."""
    rows = (await s.execute(select(*_COLUMNS).join(DocumentLink, DocumentLink.document_id == Document.id).where(
        DocumentLink.entity_type == entity_type, DocumentLink.entity_id == entity_id).order_by(Document.id))).all()
    out = []
    for r in rows:
        d = r._asdict()
        body = d.pop("body_json")
        out.append(RecordDocumentRead(**d, note=(body or {}).get("note") if d["kind"] == "ATTACHMENT" else None))
    return out


async def timeline_of(s, entity_type: str, entity_id: int, *, limit: int = 200) -> list[TimelineEntry]:
    """The record's audit, its lines' and its attachments', newest first."""
    k = kind_of(entity_type)
    table, line_table = k.model.__tablename__, k.line_model.__tablename__
    line_ids = [str(i) for i in (await s.execute(select(k.line_model.id).where(getattr(k.line_model, k.line_fk) == entity_id))).scalars()]
    trail = (await s.execute(select(AuditRecord).where(or_(
        (AuditRecord.entity == table) & (AuditRecord.entity_id == str(entity_id)),
        (AuditRecord.entity == line_table) & (AuditRecord.entity_id.in_(line_ids) if line_ids else False),
    )).order_by(AuditRecord.at.desc(), AuditRecord.id.desc()).limit(limit))).scalars()
    return [TimelineEntry(at=a.at, user_name=a.user_name, entity=a.entity, action=a.action, before=a.before, after=a.after,
                          reason=a.reason) for a in trail]


class Attachments:
    """A person's files on an order or a transfer."""

    def __init__(self, uow) -> None:
        self.uow, self.s = uow, uow.session

    async def _record(self, k: RecordKind, entity_id: int):
        rec = await self.s.get(k.model, entity_id)
        if rec is None:
            raise BusinessError("entity.not_found", f"no such {k.label}", status_code=404)
        return rec

    async def list(self, entity_type: str, entity_id: int) -> list[AttachmentRead]:
        k = kind_of(entity_type)
        await authorize(self.uow, k.read)
        await self._record(k, entity_id)
        return [AttachmentRead(**d.model_dump()) for d in await documents_of(self.s, entity_type, entity_id) if d.kind == "ATTACHMENT"]

    async def attach(self, entity_type: str, entity_id: int, *, name: str, data: bytes, note: str | None) -> AttachmentRead:
        k = kind_of(entity_type)
        await authorize(self.uow, k.change)
        rec = await self._record(k, entity_id)
        name = re.sub(r"[\\/\x00-\x1f]+", "_", (name or "").strip())[:200] or "file"
        if not data:
            raise BusinessError("attachment.empty", "the file is empty")
        if len(data) > MAX_BYTES:
            raise BusinessError("attachment.too_large", f"at most {MAX_BYTES // (1024 * 1024)} MB")
        if not ALLOWED.search(name):
            raise BusinessError("attachment.type", "this kind of file cannot be attached (PDF, pictures, mail, Office, CSV, text, EDI)")
        note = (note or "").strip()[:500] or None
        sha = hashlib.sha256(data).hexdigest()
        doc = (await self.s.execute(select(Document).where(Document.sha256 == sha, Document.content.is_not(None)))).scalar_one_or_none()
        if doc is None:
            doc = Document(direction="IN", origin="USER", system=None, kind="ATTACHMENT", remote_name=name, size=len(data), sha256=sha,
                           content=data, body_json={"note": note} if note else None,
                           created_by_name=self.uow.actor.name)
            self.s.add(doc)
            await self.s.flush()
        linked = (await self.s.execute(select(DocumentLink.id).where(DocumentLink.document_id == doc.id, DocumentLink.entity_type == k.entity_type,
                                                                    DocumentLink.entity_id == entity_id))).first()
        if linked:
            raise BusinessError("attachment.already", f"this file is already on this {k.label} ({doc.remote_name})")
        self.s.add(DocumentLink(document_id=doc.id, entity_type=k.entity_type, entity_id=entity_id))
        label = getattr(rec, "label", None) or getattr(rec, "transfer_number", entity_id)
        self.uow.set_reason(f"file attached: {name}" + (f" — {note}" if note else ""))
        await oplog.write(self.s, "INFO", "attachment", f"{k.label} {label}: {name} attached by {self.uow.actor.name}" + (f" — {note}" if note else ""),
                          document_id=doc.id, **({"order_id": entity_id} if k.entity_type == "order" else {"transfer_id": entity_id}))
        await self.s.flush()
        [out] = [d for d in await documents_of(self.s, entity_type, entity_id) if d.id == doc.id]
        return AttachmentRead(**out.model_dump())

    async def remove(self, entity_type: str, entity_id: int, document_id: int, reason: str) -> None:
        k = kind_of(entity_type)
        await authorize(self.uow, k.change)
        rec = await self._record(k, entity_id)
        doc = await self.s.get(Document, document_id)
        link = (await self.s.execute(select(DocumentLink).where(DocumentLink.document_id == document_id, DocumentLink.entity_type == k.entity_type,
                                                                DocumentLink.entity_id == entity_id))).scalar_one_or_none()
        if doc is None or link is None:
            raise BusinessError("entity.not_found", "this file is not on this record", status_code=404)
        if doc.kind != "ATTACHMENT":
            raise BusinessError("attachment.not_attachment", "only a file a person attached can be removed; EDI files stay with their record")
        await self.s.delete(link)
        label = getattr(rec, "label", None) or getattr(rec, "transfer_number", entity_id)
        self.uow.set_reason(f"attachment removed: {doc.remote_name} — {reason}")
        await oplog.write(self.s, "INFO", "attachment", f"{k.label} {label}: {doc.remote_name} removed by {self.uow.actor.name} — {reason}",
                          document_id=doc.id, **({"order_id": entity_id} if k.entity_type == "order" else {"transfer_id": entity_id}))
        await self.s.flush()
