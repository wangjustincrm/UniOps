"""While Sales runs beside Zoho: Radiant answers Zoho's 940, so its 997 matches no
940 here and the order Sales holds stays at 940 Sent / Timeout. Given a newer
Zoho export, this keeps each Zoho 940 that is not here yet as the 940 of the
order it was for (same order: Shopify number, or the retailer's PO), then reads
again the 997s that answer it, as the scheduled run does; their "997 matches no
940" tasks are closed. The order number, the lines and Sales' own 940 stay as
they are; nothing is sent and nothing is written to Shopify. Idempotent.

    python -m app.scripts.attach_zoho_940s <export folder>            # dry run
    python -m app.scripts.attach_zoho_940s <export folder> --apply
"""
from __future__ import annotations

from datetime import timezone
from pathlib import Path

from sqlalchemy import func, select

from app.core.deps import uow_factory
from app.domain.edi997 import parse_997
from app.domain.intake import classify
from app.domain.status import Ack, OutboundFile
from app.domain.x12 import X12Error
from app.models.documents import Document, DocumentLink
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.scripts.import_zoho_history import ZOHO, History, index
from app.services import oplog
from app.services.outbound import Outbound940
from app.services.problems import EDI_997_UNMATCHED, task_service


async def _order(s, c) -> Order | None:
    q = select(Order).where(Order.source_order_number == c.source_number, Order.shadow.is_(False))
    q = q.where(Order.source_type == ("EDI_850" if c.retail else "SHOPIFY"))
    rows = list((await s.execute(q.order_by(Order.id))).scalars())
    return rows[0] if len(rows) == 1 else None


async def _answers(s, gs06: str) -> list[Document]:
    """The 997s read before their 940 was here: Processed, and no 940 they could match."""
    out = []
    for d in (await s.execute(select(Document).where(Document.kind == "997_EDI", Document.direction == "IN",
                                                     Document.processing_status == "Processed"))).scalars():
        content = (await s.execute(select(Document.content).where(Document.id == d.id))).scalar_one()
        try:
            groups = parse_997(content)
        except X12Error:
            continue
        if any(g.functional_id == "OW" and g.group_control.lstrip("0") == gs06.lstrip("0") for g in groups):
            out.append(d)
    return out


async def main(args: ScriptArgs) -> dict:
    root = Path(args.positional[0])
    chains, _ = index(root)
    h = History(uow_factory, root)
    tasks = task_service(lambda: False)
    rows: list[dict] = []
    reread: list[int] = []
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        for c in chains:
            k = classify(c.f940.read_bytes())
            here = (await s.execute(select(Document.id).where(Document.kind == "940_EDI", Document.direction == "OUT",
                                                              func.ltrim(Document.gs06, "0") == k.gs06.lstrip("0")))).first()
            if here is not None:
                continue
            order = await _order(s, c)
            row = {"zoho_940": c.f940.name.split("_")[0], "order": order.label if order else None,
                   "status": order.fulfillment_status if order else None}
            rows.append(row)
            if order is None:
                row["left"] = "no single order here for it"
                continue
            acks = await _answers(s, k.gs06)
            row["997s"] = len(acks)
            if not args.apply:
                continue
            doc = await h.store(s, c.f940, kind="940_EDI", system="RADIANT", direction="OUT", at=c.sent_at, origin=ZOHO,
                                file_status=OutboundFile.SENT.value, ack_status=Ack.WAITING.value,
                                sent_at=c.sent_at.astimezone(timezone.utc), isa13=k.isa13, gs06=k.gs06, st02=k.st02,
                                partner_code=order.partner_code, connection_id=await h.conn(s, "RADIANT"),
                                body_json={"order_id": order.id, "reason": "sent by Zoho beside Sales"})
            await h.link(s, doc, order.id)
            for a in acks:
                a.processing_status, a.error = "Pending", None
                await tasks.resolve_problem(s, EDI_997_UNMATCHED, a.id)
                reread.append(a.id)
            await oplog.write(s, "INFO", "repair", f"order {order.label}: Zoho's 940 kept, {len(acks)} 997 read again",
                              order_id=order.id, document_id=doc.id)
        if args.apply:
            await u.commit()
    report = None
    if args.apply and reread:
        report = vars(await Outbound940(uow_factory=uow_factory, tasks=tasks, sftp=None).process_997s())
        async with uow_factory(SYSTEM_ACTOR) as u:
            for r in rows:
                if r.get("order"):
                    o = (await u.session.execute(select(Order.fulfillment_status).join(
                        DocumentLink, (DocumentLink.entity_id == Order.id) & (DocumentLink.entity_type == "order"))
                        .join(Document, Document.id == DocumentLink.document_id)
                        .where(Document.remote_name.like(r["zoho_940"] + "%")))).scalars().first()
                    r["now"] = o
    return {"zoho_940s_not_here": len(rows), "997s_read_again": len(reread), "997_run": report, "detail": rows}


if __name__ == "__main__":
    script_main(main)
