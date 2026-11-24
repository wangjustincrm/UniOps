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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select

from app.models.documents import Document, DocumentLink
from app.models.invoices import Invoice, InvoiceLine
from app.models.orders import Order, OrderLine
from app.models.records import AuditRecord
from app.models.transfers import Transfer, TransferLine
from app.platform.services import Service
from app.platform.errors import BusinessError
from app.schemas.orders import TimelineEntry
from app.schemas.records import AttachmentRead, RecordDocumentRead
from app.platform.audit import audit_also
from app.services import oplog

MAX_BYTES = 20 * 1024 * 1024
# What a person may attach: documents, pictures, mail, spreadsheets, text (no programs, no archives).
EXTENSIONS = ("pdf", "png", "jpg", "jpeg", "gif", "webp", "heic", "tif", "tiff", "txt", "csv", "xls", "xlsx", "doc", "docx", "ppt", "pptx",
              "msg", "eml", "edi", "json", "xml")


@dataclass(frozen=True)
class RecordKind:
    entity_type: str        # the DocumentLink entity_type
    model: type
    line_model: type
    line_fk: str
    read: str
    change: str             # the permission to attach / remove
    label: str
    max_bytes: int = MAX_BYTES                # what /meta says is what is enforced: the kind's own, here and in the route
    extensions: tuple[str, ...] = EXTENSIONS
    kinds: str = "PDF, pictures, mail, Office, CSV, text, EDI"  # the extensions, in words (a refusal)

    def accepts(self, name: str) -> bool:
        return name.lower().endswith(tuple(f".{e}" for e in self.extensions))


# Declared on the record's list (Resource.attachments); tests/test_list_resources.py holds the two together.
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


def link_audit(entity_type: str, entity_id: int):
    """The audit rows of a record's file links (attached, removed): the link's own row names the record."""
    def names(row):
        return (row["entity_type"].astext == entity_type) & (row["entity_id"].astext == str(entity_id))
    # a delete's `after` is JSON null, not SQL NULL: look in both
    return (AuditRecord.entity == DocumentLink.__tablename__) & or_(names(AuditRecord.after), names(AuditRecord.before))


@dataclass(frozen=True)
class RelatedField:
    """One field of another record that says something of this one (an invoice's status is its order's column): the other
    record's updates of that field, and nothing else of it."""
    model: type
    via: str                # this record's column that holds the other's id
    field: str
    word: str


@dataclass(frozen=True)
class TimelineKind:
    """What a record's Activity is read from: its own audit rows and its lines' (and its file links', where it has any).
    A declaration of its own (Resource.timeline): it says nothing of attachments."""
    model: type
    line_model: type
    line_fk: str
    # A field → the word people read. None: the fields as they are named (orders, transfers). Given: only the record's own
    # columns, in words; a value computed from another record is not a change of this one, and an internal id says nothing.
    words: Mapping[str, str] | None = None
    internal: frozenset[str] = frozenset()
    line_words: Mapping[str, str] | None = None  # the same for its lines: only these fields, and the entry says which line
    files: frozenset[str] = frozenset()          # its columns that hold a document's id: the file's name is shown
    related: tuple[RelatedField, ...] = ()


TIMELINES = {
    "order": TimelineKind(Order, OrderLine, "order_id"),
    "transfer": TimelineKind(Transfer, TransferLine, "transfer_id"),
    "invoice": TimelineKind(Invoice, InvoiceLine, "invoice_id", internal=frozenset({"id", "order_id"}),
                            words={"invoice_number": "Invoice", "document_id": "Document", "send_count": "Times sent", "sent_at": "Sent at",
                                   "error": "Problem", "invoice_date": "Invoice date", "delivery_number": "Delivery no.",
                                   "payment_terms": "Payment terms", "method": "Method", "currency": "Currency", "subtotal": "Subtotal",
                                   "total": "Total"},
                            line_words={"qty": "Quantity", "unit_price": "Unit price", "amount": "Amount"}, files=frozenset({"document_id"}),
                            related=(RelatedField(Order, "order_id", "invoice_status", "Status"),)),
}


def _in_words(k: TimelineKind, entity: str, values: dict | None, files: Mapping[int, str | None]) -> dict | None:
    """The fields of the record's own row, its lines' and its related field as people read them; another row's (a file
    link's) as it is."""
    if values is None:
        return None
    for r in k.related:
        if entity == r.model.__tablename__:
            return {r.word: values.get(r.field)}
    if entity == k.line_model.__tablename__ and k.line_words is not None:
        return {w: values[f] for f, w in k.line_words.items() if f in values}
    if k.words is None or entity != k.model.__tablename__:
        return values
    own = {c.key for c in k.model.__table__.columns} - k.internal
    return {k.words.get(f, f): (files.get(v) if f in k.files else v) for f, v in values.items() if f in own}


def _about(k: TimelineKind, a: AuditRecord, lines: Mapping[str, Any]) -> str | None:
    """Which line an entry is of, where the lines' fields are in words: "Line 1 · CF0092"."""
    ln = lines.get(a.entity_id) if k.line_words is not None and a.entity == k.line_model.__tablename__ else None
    return f"Line {ln.line_no}" + (f" · {ln.item_code}" if ln.item_code else "") if ln is not None else None


async def timeline_of(s, entity_type: str, entity_id: int, *, limit: int = 200) -> list[TimelineEntry]:
    """The record's audit, its lines' and its attachments', newest first."""
    k = TIMELINES[entity_type]
    table, line_table = k.model.__tablename__, k.line_model.__tablename__
    lm = k.line_model
    lines = {str(r.id): r for r in (await s.execute(select(lm.id, lm.line_no, lm.item_code).where(getattr(lm, k.line_fk) == entity_id))).all()}
    related = []
    for r in k.related:
        other = (await s.execute(select(getattr(k.model, r.via)).where(k.model.id == entity_id))).scalar_one_or_none()
        related.append((AuditRecord.entity == r.model.__tablename__) & (AuditRecord.entity_id == str(other))
                       & (AuditRecord.action == "update") & AuditRecord.after.has_key(r.field))
    trail = list((await s.execute(select(AuditRecord).where(or_(
        (AuditRecord.entity == table) & (AuditRecord.entity_id == str(entity_id)),
        (AuditRecord.entity == line_table) & (AuditRecord.entity_id.in_(lines) if lines else False),
        *related,
        *([link_audit(entity_type, entity_id)] if entity_type in RECORDS else []),
    )).order_by(AuditRecord.at.desc(), AuditRecord.id.desc()).limit(limit))).scalars())
    used = {v for a in trail if a.entity == table for side in (a.before, a.after) for f, v in (side or {}).items() if f in k.files and v}
    files = dict((await s.execute(select(Document.id, Document.remote_name).where(Document.id.in_(used)))).all()) if used else {}
    return [TimelineEntry(at=a.at, user_name=a.user_name, entity=a.entity, action=a.action, audit_log_id=a.audit_log_id,
                          about=_about(k, a, lines),
                          before=_in_words(k, a.entity, a.before, files), after=_in_words(k, a.entity, a.after, files), reason=a.reason) for a in trail]


class Attachments(Service):
    """A person's files on an order or a transfer."""

    def __init__(self, uow) -> None:
        super().__init__(uow)

    async def _record(self, k: RecordKind, entity_id: int):
        rec = await self.s.get(k.model, entity_id)
        if rec is None:
            raise BusinessError("entity.not_found", f"no such {k.label}", status_code=404)
        return rec

    async def list(self, entity_type: str, entity_id: int) -> list[AttachmentRead]:
        k = kind_of(entity_type)
        await self._authorize(k.read)
        await self._record(k, entity_id)
        return [AttachmentRead(**d.model_dump()) for d in await documents_of(self.s, entity_type, entity_id) if d.kind == "ATTACHMENT"]

    async def attach(self, entity_type: str, entity_id: int, *, name: str, data: bytes, note: str | None) -> AttachmentRead:
        k = kind_of(entity_type)
        await self._authorize(k.change)
        rec = await self._record(k, entity_id)
        name = re.sub(r"[\\/\x00-\x1f]+", "_", (name or "").strip())[:200] or "file"
        if not data:
            raise BusinessError("attachment.empty", "the file is empty")
        if len(data) > k.max_bytes:
            raise BusinessError("attachment.too_large", f"at most {k.max_bytes // (1024 * 1024)} MB")
        if not k.accepts(name):
            raise BusinessError("attachment.type", f"this kind of file cannot be attached ({k.kinds})")
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
        new_link = DocumentLink(document_id=doc.id, entity_type=k.entity_type, entity_id=entity_id)
        self.s.add(new_link)
        audit_also(self.s, "create", new_link)  # in the record's Activity (timeline_of)
        label = getattr(rec, "label", None) or getattr(rec, "transfer_number", entity_id)
        self.uow.set_reason(f"file attached: {name}" + (f" — {note}" if note else ""))
        await oplog.write(self.s, "INFO", "attachment", f"{k.label} {label}: {name} attached by {self.uow.actor.name}" + (f" — {note}" if note else ""),
                          document_id=doc.id, **({"order_id": entity_id} if k.entity_type == "order" else {"transfer_id": entity_id}))
        await self.s.flush()
        [out] = [d for d in await documents_of(self.s, entity_type, entity_id) if d.id == doc.id]
        return AttachmentRead(**out.model_dump())

    async def remove(self, entity_type: str, entity_id: int, document_id: int, reason: str) -> None:
        k = kind_of(entity_type)
        await self._authorize(k.change)
        rec = await self._record(k, entity_id)
        doc = await self.s.get(Document, document_id)
        link = (await self.s.execute(select(DocumentLink).where(DocumentLink.document_id == document_id, DocumentLink.entity_type == k.entity_type,
                                                                DocumentLink.entity_id == entity_id))).scalar_one_or_none()
        if doc is None or link is None:
            raise BusinessError("entity.not_found", "this file is not on this record", status_code=404)
        if doc.kind != "ATTACHMENT":
            raise BusinessError("attachment.not_attachment", "only a file a person attached can be removed; EDI files stay with their record")
        audit_also(self.s, "delete", link)  # in the record's Activity (timeline_of); the file itself stays
        await self.s.delete(link)
        label = getattr(rec, "label", None) or getattr(rec, "transfer_number", entity_id)
        self.uow.set_reason(f"attachment removed: {doc.remote_name} — {reason}")
        await oplog.write(self.s, "INFO", "attachment", f"{k.label} {label}: {doc.remote_name} removed by {self.uow.actor.name} — {reason}",
                          document_id=doc.id, **({"order_id": entity_id} if k.entity_type == "order" else {"transfer_id": entity_id}))
        await self.s.flush()
