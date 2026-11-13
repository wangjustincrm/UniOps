"""A 944 for a customer's return (user 2026-11-13): Radiant receives returned goods with a 944 whose
transfer key is "RA CRM<n>" — n is the Shopify order number (RA CRM8961 → SHP-8961; checked on all
three received so far). It is no transfer: it is kept as the file it is, linked to its order, the
returned quantity is shown on the order's lines (Returned EA), and the inventory takes it back at
Radiant (stock sync). No task — unless no order has that number (then a person looks)."""
from __future__ import annotations

import re

from sqlalchemy import select

from app.domain.status import INBOUND_FILE, InboundFile
from app.models.documents import Document, DocumentLink
from app.models.orders import Order, OrderLine
from app.services import oplog
from app.services.doc_links import link
from app.services.problems import EDI_944_NO_TRANSFER, problem

RA = re.compile(r"^\s*RA\s*CRM\s*0*(\d+)\s*$", re.I)
RA_ERROR = "a return (RA)"


def ra_number(key: str | None) -> str | None:
    """The Shopify order number of an RA key ("RA CRM8961" → "8961"), else None."""
    m = RA.match(key or "")
    return m.group(1) if m else None


def is_return(key: str | None) -> bool:
    return (key or "").strip().upper().startswith("RA")


async def apply_return_944(uow, doc: Document, r, *, tasks=None) -> str:
    """Link the RA 944 to its order and show what came back; the file stays Skipped (it is no transfer)."""
    s = uow.session
    n = ra_number(r.transfer_key)
    o = None
    if n:
        o = (await s.execute(select(Order).where(Order.source_type == "SHOPIFY", Order.source_order_number.in_([n, f"#{n}"]))
                             .order_by(Order.id).limit(1))).scalar_one_or_none()
    if doc.processing_status == InboundFile.PROCESSING.value:
        INBOUND_FILE.transition(doc, InboundFile.SKIPPED, actor=uow.actor, events=uow.events, reason="a customer's return (RA), no transfer")
    if o is None:
        doc.error = f"{RA_ERROR}: {r.transfer_key} — no order {n or '?'} in Sales"
        if tasks is not None:
            await tasks.raise_problem(s, EDI_944_NO_TRANSFER, doc.id, problem(
                EDI_944_NO_TRANSFER, f"Return 944 matches no order: {r.transfer_key}"[:255],
                f"944 file {doc.remote_name}: a customer's return ({r.transfer_key}) for Shopify order {n or '?'}, which is not in "
                f"Sales. It is kept as a file; find the order or tell the warehouse."))
        return "return_no_order"
    already = (await s.execute(select(DocumentLink.id).where(DocumentLink.document_id == doc.id, DocumentLink.entity_type == "order",
                                                            DocumentLink.entity_id == o.id))).first() is not None
    doc.error = f"{RA_ERROR}: returned to Radiant for order {o.ref}"
    if already:
        return "return_known"
    await link(s, doc.id, "order", o.id)
    lines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == o.id, OrderLine.removed.is_(False))
                                  .order_by(OrderLine.line_no).with_for_update())).scalars())
    back = []
    for row in r.rows:
        ln = next((x for x in lines if x.item_code and x.item_code == row.item_code), None)
        if ln is None:
            back.append(f"{row.item_code} {row.qty} EA (not on the order)")
            continue
        cap = int(ln.shipped_ea or ln.qty_ea or 0)
        ln.returned_ea = min(cap, int(ln.returned_ea or 0) + int(row.qty)) if cap else int(ln.returned_ea or 0) + int(row.qty)
        back.append(f"{row.item_code} {row.qty} EA")
    uow.set_reason(f"returned to Radiant: 944 {doc.remote_name} ({r.transfer_key})")
    await oplog.write(s, "INFO", "transfer", f"order {o.label}: customer's return received at Radiant ({r.transfer_key}): "
                      f"{', '.join(back) or 'nothing listed'}", document_id=doc.id, order_id=o.id)
    return "return_linked"
