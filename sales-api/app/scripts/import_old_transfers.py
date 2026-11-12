"""The old system's transfers, made from their 944s (user 2026-11-12: "fetch the data from old system files").

A 944 of the old system was kept as a file only (Skipped: "its transfer is not in Sales") —
import_old_files could not read it, it had no transfer to answer. This makes that transfer from
the 944 itself, so it is in Transfers with its lines, its receipt and its lots, and prints:
  - number: the 944's transfer key (W17-04) as the old system wrote it (00008, RA CRM4750, …);
    Sales' own numbers already start above them (sales_transfer_number_seq);
  - from: the transfer source warehouse (the 944's ship-from, N1*SF), to: the warehouse whose
    EDI id is the 944's N1*WH;
  - one line per item, its quantity what the 944 received (EA): Received, line by line;
  - the receipt and its rows (lot, description) as Sales' own 944 step keeps them, applied,
    and the 944 linked to the transfer and Processed;
  - dated the receipt date. No 943 is made, nothing is sent, no task is raised.
A key already a transfer here, or a 944 already read, is left (and said). Idempotent.

    python -m app.scripts.import_old_transfers            # dry run: what each 944 would become
    python -m app.scripts.import_old_transfers --apply
"""
from __future__ import annotations

import collections
from datetime import datetime, time

from sqlalchemy import select

from app.core.deps import uow_factory
from app.domain.edi944 import LineIn, allocate, parse_944
from app.domain.status import InboundFile
from app.domain.status import Transfer as T
from app.domain.x12 import X12Error, segments
from app.models.config import Item, Warehouse
from app.models.documents import Document
from app.models.transfers import Receipt, ReceiptRow, Transfer, TransferLine
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.services import oplog
from app.services.doc_links import link
from app.services.outbound import TORONTO

OLD = "old system's 944"


def _wh_code(data: bytes) -> str | None:
    """The receiving warehouse's id: N1*WH*name*qualifier*id."""
    for s in segments(data):
        if s[0] == "N1" and len(s) > 4 and s[1] == "WH":
            return s[4].strip() or None
    return None


async def main(args: ScriptArgs) -> dict:
    n: collections.Counter = collections.Counter()
    detail: list[dict] = []
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        docs = list((await s.execute(select(Document).where(
            Document.kind == "944_EDI", Document.direction == "IN", Document.processing_status == InboundFile.SKIPPED.value,
            Document.error.like(f"{OLD}%")).order_by(Document.created_at, Document.id).with_for_update())).scalars())
        source = (await s.execute(select(Warehouse).where(Warehouse.role == "TRANSFER_SOURCE", Warehouse.active.is_(True))
                                  .order_by(Warehouse.id))).scalars().all()
        warehouses = {w.edi_id: w for w in (await s.execute(select(Warehouse))).scalars() if w.edi_id}
        items = set((await s.execute(select(Item.item_code))).scalars())
        for doc in docs:
            content = (await s.execute(select(Document.content).where(Document.id == doc.id))).scalar_one()
            row: dict = {"document": doc.id, "file": doc.remote_name}
            detail.append(row)
            try:
                r = parse_944(bytes(content))
                to = warehouses.get(_wh_code(bytes(content)) or "")
            except X12Error as e:
                row["result"] = n_add(n, f"left: cannot be read ({e})")
                continue
            key = (r.transfer_key or "").strip()
            row.update(transfer=key, received=str(r.receipt_date), total_ea=r.total)
            if not key or len(key) > 10:
                row["result"] = n_add(n, "left: no transfer key (or longer than 10)")
                continue
            if len(source) != 1 or to is None:
                row["result"] = n_add(n, "left: the source or the receiving warehouse is not set up")
                continue
            if (await s.execute(select(Transfer.id).where(Transfer.transfer_number == key))).first():
                row["result"] = n_add(n, "left: a transfer with this number is already here")
                continue
            if (await s.execute(select(Receipt.id).where(Receipt.document_id == doc.id))).first():
                row["result"] = n_add(n, "left: already read")
                continue
            unknown = sorted({x.item_code or f"UPC {x.upc}" for x in r.rows if x.item_code not in items})
            if unknown:
                row["result"] = n_add(n, "left: item(s) not in the item master")
                row["unknown_items"] = unknown
                continue
            qty: dict[str, int] = {}
            for x in r.rows:
                qty[x.item_code] = qty.get(x.item_code, 0) + x.qty
            row["lines"] = qty
            row["result"] = n_add(n, "made: Received" if args.apply else "would be made: Received")
            if not args.apply:
                continue
            at = datetime.combine(r.receipt_date, time(12), tzinfo=TORONTO) if r.receipt_date else doc.created_at
            t = Transfer(transfer_number=key, from_warehouse_id=source[0].id, to_warehouse_id=to.id, status=T.RECEIVED.value,
                         notes="Old system's transfer, made from its 944 (history): no 943 in Sales.")
            s.add(t)
            await s.flush()
            t.created_at = at
            lines = []
            for i, (code, q) in enumerate(qty.items(), start=1):
                ln = TransferLine(transfer_id=t.id, line_no=i, item_code=code, qty_ea=q)
                s.add(ln)
                lines.append(ln)
            await s.flush()
            a = allocate([LineIn(ln.line_no, ln.item_code, ln.qty_ea) for ln in lines], r.rows, r.total)
            for ln in lines:
                ln.received_ea = a.received.get(ln.line_no, 0)
                ln.line_status = a.line_status.get(ln.line_no, "Open")
            if a.outcome != T.RECEIVED.value:  # the lines are the 944's own: only a bad total gets here
                t.status = a.outcome
                row["result"] = f"made: {a.outcome}"
            rec = Receipt(document_id=doc.id, transfer_id=t.id, transfer_key_received=key, receipt_date=r.receipt_date,
                          total_ea=r.total, applied=True, shadow=False, outcome=a.outcome, discrepancies=a.discrepancies or None)
            s.add(rec)
            await s.flush()
            for i, (x, line_no) in enumerate(zip(r.rows, a.row_line), start=1):
                s.add(ReceiptRow(receipt_id=rec.id, position=i, lx_number=x.lx_number, item_code=x.item_code, upc=x.upc,
                                 lot=x.lot, description=x.description, qty_ea=x.qty, transfer_line_no=line_no))
            await link(s, doc.id, "transfer", t.id)
            doc.processing_status = InboundFile.PROCESSED.value
            doc.error = None
            await oplog.write(s, "INFO", "repair", f"transfer {key} made from the old system's 944 {doc.remote_name}",
                              document_id=doc.id)
        if args.apply:
            await oplog.write(s, "INFO", "repair", "old system's transfers made from their 944s", context=dict(n))
            await u.commit()
    return {"files": len(detail), "outcome": dict(n), "detail": detail}


def n_add(n: collections.Counter, what: str) -> str:
    n[what] += 1
    return what


if __name__ == "__main__":
    script_main(main)
