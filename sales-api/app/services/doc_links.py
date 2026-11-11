"""A document belongs to an order or a transfer once (uq_sales_document_links). Every place that links
one goes through here: a file read again (a reparse, a resend, Zoho's history) never fails on the
link it already has (2026-11-11: a 997 read twice stayed Pending forever, retried every cycle)."""
from sqlalchemy import select

from app.models.documents import DocumentLink


async def link(session, document_id: int, entity_type: str, entity_id: int) -> None:
    exists = (await session.execute(select(DocumentLink.id).where(
        DocumentLink.document_id == document_id, DocumentLink.entity_type == entity_type,
        DocumentLink.entity_id == entity_id))).first()
    if exists is None:
        session.add(DocumentLink(document_id=document_id, entity_type=entity_type, entity_id=entity_id))
