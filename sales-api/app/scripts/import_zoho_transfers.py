"""Zoho's transfers (its 943s) with their answers (997s, 944s), from a folder of those files
(user 2026-11-12: the transfers of the first export, uploaded again).

Each file goes through Sales' own steps, at its own time, sending nothing:
  - a 943 whose transfer is not here: the transfer, with Zoho's number and lines, its 943 kept
    as sent by Zoho (Waiting for 997); then its 997s and its 944 (944-*-<number>*) read as Sales
    reads them — Received, Partially Received or Discrepancy, as the warehouse answered;
  - a 943 whose transfer is already here (made from the old system's 944): the 943 is kept with
    it, as sent by Zoho, its acknowledgement the 944 already read; nothing else changes;
  - a 944 of no 943 in the folder: one already here is left; another is kept as the old
    system's file and made into its transfer by import_old_transfers (Received, from the 944).
Tasks are counted, never raised (history). Idempotent.

    python -m app.scripts.import_zoho_transfers <folder>            # dry run: what each file would become
    python -m app.scripts.import_zoho_transfers <folder> --apply    # needs automatic runs off and the shadow run off
"""
from __future__ import annotations

import collections
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.deps import uow_factory
from app.domain.edi944 import LineIn, allocate, parse_944
from app.domain.intake import classify
from app.domain.status import TRANSFER, Ack, OutboundFile
from app.domain.status import Transfer as T
from app.domain.x12 import segments
from app.models.config import Warehouse
from app.models.documents import Document, DocumentLink
from app.models.transfers import Receipt, Transfer
from app.platform.actors import SYSTEM_ACTOR
from app.scripts import import_old_transfers
from app.scripts.base import ScriptArgs, script_main
from app.scripts.import_zoho_history import TOR, ZOHO, History, envelope_time, file_time, refuse_unless_quiet
from app.services import oplog

OLD = import_old_transfers.OLD


def _943(data: bytes) -> tuple[str, list[dict]]:
    seg = segments(data)
    number = next(x for x in seg if x[0] == "W06")[2].strip()
    return number, [{"item_code": x[5].strip(), "qty_ea": int(float(x[1]))} for x in seg if x[0] == "W04"]


def _key_of_944(data: bytes) -> str:
    return (parse_944(data).transfer_key or "").strip()


def _at(f: Path, data: bytes) -> datetime:
    return envelope_time(data) or file_time(f.name) or datetime.now(TOR)


async def main(args: ScriptArgs) -> dict:
    root = Path(args.positional[0])
    files = sorted(p for p in root.iterdir() if p.is_file())
    f943 = [f for f in files if f.name.upper().startswith("CRM-943")]
    f944 = {f: _key_of_944(f.read_bytes()) for f in files if f.name.startswith("944")}
    f997 = [f for f in files if f.name.startswith("997")]
    numbers = {}
    for f in f943:
        number, lines = _943(f.read_bytes())
        numbers[number] = (f, lines)
    n: collections.Counter = collections.Counter()
    detail: list[dict] = []
    if args.apply:
        await refuse_unless_quiet(uow_factory)
    h = History(uow_factory, root)
    for number, (f, lines) in sorted(numbers.items()):
        data = f.read_bytes()
        answers = [g for g, k in f944.items() if k == number or k.zfill(5) == number]
        acks = [g for g in f997 if f"-{int(number)}-" in g.name] if number.isdigit() else []
        row: dict = {"transfer": number, "943": f.name, "lines": {x["item_code"]: x["qty_ea"] for x in lines},
                     "944": [g.name for g in answers], "997": [g.name for g in acks]}
        detail.append(row)
        async with uow_factory(SYSTEM_ACTOR) as u:
            s = u.session
            t = (await s.execute(select(Transfer).where(Transfer.transfer_number == number).with_for_update())).scalar_one_or_none()
            if t is not None:
                here = await s.get(Document, t.document_id) if t.document_id else None
                if here is not None and here.sha256 == hashlib.sha256(data).hexdigest():
                    row["result"] = "left: its 943 is already kept with it"
                    n[row["result"]] += 1
                    continue
                received = (await s.execute(select(Document.remote_name).join(Receipt, Receipt.document_id == Document.id)
                                            .where(Receipt.transfer_id == t.id, Receipt.applied.is_(True)))).scalars().first()
                row["result"] = f"transfer already here ({t.status}): its 943 kept with it"
                n[row["result"]] += 1
                if args.apply:
                    at = _at(f, data)
                    k = classify(data)
                    doc = await h.store(s, f, kind="943_EDI", system="RADIANT", direction="OUT", at=at, data=data, origin=ZOHO,
                                        file_status=OutboundFile.SENT.value, sent_at=at.astimezone(timezone.utc),
                                        ack_status=Ack.ACCEPTED.value if received else Ack.NOT_REQUIRED.value,
                                        ack_detail=f"accepted by 944 {received}" if received else "sent by Zoho: no 997 in the export",
                                        isa13=k.isa13, gs06=k.gs06, st02=k.st02, connection_id=await h.conn(s, "RADIANT"),
                                        body_json={"transfer_id": t.id, "reason": "sent by Zoho (history)"})
                    if (await s.execute(select(DocumentLink.id).where(DocumentLink.document_id == doc.id, DocumentLink.entity_type == "transfer",
                                                                     DocumentLink.entity_id == t.id))).first() is None:
                        s.add(DocumentLink(document_id=doc.id, entity_type="transfer", entity_id=t.id))
                    t.document_id = doc.id
                    await oplog.write(s, "INFO", "repair", f"transfer {number}: Zoho's 943 {f.name} kept with it", document_id=doc.id)
                    await u.commit()
                continue
            if not args.apply:
                if answers:
                    r = parse_944(answers[0].read_bytes())
                    a = allocate([LineIn(i, x["item_code"], x["qty_ea"]) for i, x in enumerate(lines, start=1)], r.rows, r.total)
                    row["result"] = f"would be made: {a.outcome}"
                    if a.discrepancies:
                        row["discrepancies"] = a.discrepancies
                else:
                    row["result"] = "would be made: Waiting for 997 (no 944 in the folder)"
                n[row["result"]] += 1
                continue
            at = _at(f, data)
            h.at(at)
            roles = {w.role: w.id for w in (await s.execute(select(Warehouse).where(Warehouse.active.is_(True)))).scalars()}
            t = Transfer(transfer_number=number, from_warehouse_id=roles["TRANSFER_SOURCE"], to_warehouse_id=roles["WAREHOUSE"],
                         status=T.DRAFT.value, notes="Zoho's transfer (history): its 943 sent by Zoho.", version=1)
            s.add(t)
            await s.flush()
            t.created_at = at.astimezone(timezone.utc)
            h.transfers_._lines(s, t, lines)
            for to in (T.READY_TO_SEND, T.GENERATED_943, T.WAITING_FOR_997):
                TRANSFER.transition(t, to, actor=u.actor, events=u.events, reason="943 sent by Zoho (history)")
            k = classify(data)
            doc = await h.store(s, f, kind="943_EDI", system="RADIANT", direction="OUT", at=at, data=data, origin=ZOHO,
                                file_status=OutboundFile.SENT.value, ack_status=Ack.WAITING.value,
                                sent_at=at.astimezone(timezone.utc), isa13=k.isa13, gs06=k.gs06, st02=k.st02,
                                connection_id=await h.conn(s, "RADIANT"), body_json={"transfer_id": t.id, "reason": "sent by Zoho (history)"})
            s.add(DocumentLink(document_id=doc.id, entity_type="transfer", entity_id=t.id))
            t.document_id = doc.id
            tid = t.id
            await u.commit()
        for g in acks + answers:
            await h.read_file(g)
        async with uow_factory(SYSTEM_ACTOR) as u:
            t = await u.session.get(Transfer, tid)
            if not answers and not acks:  # nothing will answer it now: Zoho's, done outside Sales
                d = await u.session.get(Document, t.document_id)
                d.ack_status, d.ack_detail = Ack.NOT_REQUIRED.value, "sent by Zoho: no 997 in the export"
            await oplog.write(u.session, "INFO", "repair", f"transfer {number} made from Zoho's 943 {f.name}: {t.status}",
                              document_id=t.document_id)
            row["result"] = f"made: {t.status}"
            await u.commit()
        n[row["result"]] += 1
    # The 944s that answer no 943 here: the old system's (or already read).
    rest = [g for g, k in f944.items() if k not in numbers and k.zfill(5) not in numbers]
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        for g in rest:
            data = g.read_bytes()
            row = {"944": g.name, "transfer": f944[g]}
            detail.append(row)
            if (await s.execute(select(Document.id).where(Document.sha256 == hashlib.sha256(data).hexdigest()))).first():
                row["result"] = "left: this 944 is already here"
            elif (await s.execute(select(Transfer.id).where(Transfer.transfer_number == f944[g]))).first():
                row["result"] = "left: its transfer is already here (a different 944)"
            else:
                row["result"] = "kept, then made into its transfer (old system's)" if args.apply else "would be made from the 944: Received"
                if args.apply:
                    await h.store(s, g, kind="944_EDI", system="RADIANT", direction="IN", at=_at(g, data), data=data,
                                  processing_status="Skipped", error=f"{OLD}: its transfer is not in Sales",
                                  connection_id=await h.conn(s, "RADIANT"))
            n[row["result"]] += 1
        if args.apply:
            await u.commit()
    made = await import_old_transfers.main(ScriptArgs(("--apply",))) if args.apply else None
    if args.apply:
        async with uow_factory(SYSTEM_ACTOR) as u:
            await oplog.write(u.session, "INFO", "repair", f"Zoho's transfers read from {root.name}",
                              context={"outcome": dict(n), "would_be_tasks": dict(h.problems.raised)})
            await u.commit()
    return {"outcome": dict(n), "would_be_tasks": dict(h.problems.raised),
            "old_944s": made and made["outcome"], "detail": detail}


if __name__ == "__main__":
    script_main(main)
