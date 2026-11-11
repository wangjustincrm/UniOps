"""Files of the old system (before Zoho's 940 naming), kept with the orders they belong to.

Given a folder of X12 files (any names, any sub-folders; a UTF-8 byte order mark is fine):
  - a 940 (sent by the old system) is kept as a 940 of its order: Shopify by W05-02
    (or "Shopify_<number>" in its name), a retailer by its PO (W05-03); stored at its
    envelope time, Sent, its ack not awaited (no 997 was kept). The order itself is
    not changed. A 940 already here (same file, or same ISA13 + GS06 + ST02) is skipped.
  - a 945 of an order Sales holds is read as the warehouse sent it, at its time, through
    Sales' own 945 step (problems counted, never raised).
  - a 944 (its transfer is the old system's) and a 945 of no order here are kept as files
    only (Skipped, with why): read, they would wait for ever for what will not come.
  - a 997 Sales sent (the sender is us) is kept as the file it was.
Nothing is sent and nothing is written to Shopify. Idempotent.

    python -m app.scripts.import_old_files <folder>            # dry run: what each file would become
    python -m app.scripts.import_old_files <folder> --apply    # needs automatic runs off and the shadow run off
"""
from __future__ import annotations

import collections
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.core.deps import uow_factory
from app.domain.intake import classify
from app.domain.status import Ack, OutboundFile
from app.domain.x12 import X12Error, segments
from app.models.documents import Document
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.scripts.import_zoho_history import TOR, ZOHO, History, refuse_unless_quiet
from app.services import oplog

US = ("CRM", "CAN")  # our ISA06 / GS02 starts so (Canada Royal Milk)
WHO = "Old system history"


def envelope_at(segs: list[list[str]]) -> datetime:
    isa = segs[0]
    return datetime.strptime(isa[9].strip() + isa[10].strip(), "%y%m%d%H%M").replace(tzinfo=TOR)


def set_type(segs: list[list[str]]) -> str | None:
    st = next((s for s in segs if s[0] == "ST"), None)
    return st[1] if st and len(st) > 1 else None


def from_us(segs: list[list[str]]) -> bool:
    gs = next((s for s in segs if s[0] == "GS"), None)
    isa06 = segs[0][6].strip().upper() if len(segs[0]) > 6 else ""
    return isa06.startswith(US) or bool(gs and len(gs) > 2 and gs[2].strip().upper().startswith(US))


def order_key(path: Path, segs: list[list[str]]) -> tuple[str, str] | None:
    """(source type, source number) of a 940: a retailer's PO in W05-03, else the Shopify number."""
    w05 = next((s for s in segs if s[0] == "W05"), None)
    if w05 is None:
        return None
    po = w05[3].strip() if len(w05) > 3 else ""
    if po:
        return "EDI_850", po
    m = re.search(r"Shopify_(\d+)", path.name)
    number = m.group(1) if m else (w05[2].strip().lstrip("0") if len(w05) > 2 else "")
    return ("SHOPIFY", number) if number else None


async def _940_here(s, k) -> bool:
    return (await s.execute(select(Document.id).where(
        Document.kind == "940_EDI", Document.direction == "OUT", Document.isa13 == k.isa13,
        func.ltrim(Document.gs06, "0") == (k.gs06 or "").lstrip("0"),
        func.ltrim(Document.st02, "0") == (k.st02 or "").lstrip("0")))).first() is not None


async def main(args: ScriptArgs) -> dict:
    root = Path(args.positional[0])
    if args.apply:
        await refuse_unless_quiet(uow_factory)
    h = History(uow_factory, root)
    n: collections.Counter = collections.Counter()
    files = []
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        data = f.read_bytes()
        try:
            segs = segments(data)
        except X12Error:
            n["not an X12 file (left)"] += 1
            continue
        files.append((envelope_at(segs), f, data, segs))
    for at, f, data, segs in sorted(files, key=lambda x: (x[0], x[1].name)):
        t = set_type(segs)
        h.at(at)
        async with uow_factory(SYSTEM_ACTOR) as u:
            s = u.session
            sha_here = (await s.execute(select(Document.id).where(Document.sha256 == hashlib.sha256(data).hexdigest()))).first() is not None
        if sha_here:
            n[f"{t}: already here"] += 1
            continue
        if t == "940":
            key = order_key(f, segs)
            async with uow_factory(SYSTEM_ACTOR) as u:
                s = u.session
                k = classify(data)
                if await _940_here(s, k):
                    n["940: same control numbers already here"] += 1
                    continue
                orders = list((await s.execute(select(Order).where(Order.source_type == key[0], Order.source_order_number == key[1])
                                               .order_by(Order.id))).scalars()) if key else []
                if len(orders) != 1:
                    n[f"940: {'no' if not orders else 'several'} order {key[0] + ' ' + key[1] if key else '(no W05)'}"] += 1
                    continue
                o = orders[0]
                n[f"940: kept with its order ({'historical' if o.historical else 'open'} {o.fulfillment_status})"] += 1
                if not args.apply:
                    continue
                doc = await h.store(s, f, kind="940_EDI", system="RADIANT", direction="OUT", at=at, data=data, origin=ZOHO,
                                    file_status=OutboundFile.SENT.value, ack_status=Ack.NOT_REQUIRED.value,
                                    ack_detail="sent by the old system: no 997 was kept", sent_at=at.astimezone(timezone.utc),
                                    isa13=k.isa13, gs06=k.gs06, st02=k.st02, partner_code=o.partner_code,
                                    connection_id=await h.conn(s, "RADIANT"), created_by_name=WHO,
                                    body_json={"order_id": o.id, "reason": "sent by the old system (history)"})
                await h.link(s, doc, o.id)
                await u.commit()
            continue
        if t == "997" and from_us(segs):
            n["997 we sent: kept as a file"] += 1
            if args.apply:
                async with uow_factory(SYSTEM_ACTOR) as u:
                    await h.store(u.session, f, kind="997_EDI", system="TRUECOMMERCE", direction="OUT", at=at, data=data,
                                  origin=ZOHO, file_status=OutboundFile.SENT.value, sent_at=at.astimezone(timezone.utc),
                                  created_by_name=WHO)
                    await u.commit()
            continue
        if t in ("944", "945"):
            kind = f"{t}_EDI"
            held = None
            if t == "945":
                po = next((s[3].strip() for s in segs if s[0] == "W06" and len(s) > 3), "")
                async with uow_factory(SYSTEM_ACTOR) as u:
                    held = (await u.session.execute(select(Order.id).where(Order.source_order_number == po))).first() if po else None
            if held is None:
                # A 944's transfer, or a 945's order, of the old system is not in Sales and never will be: read, it
                # would wait for it for ever (a Health warning). Kept as the file it is, with why.
                what = "transfer" if t == "944" else "order"
                n[f"{t} of no {what} here: kept as a file"] += 1
                if args.apply:
                    async with uow_factory(SYSTEM_ACTOR) as u:
                        await h.store(u.session, f, kind=kind, system="RADIANT", direction="IN", at=at, data=data,
                                      processing_status="Skipped", error=f"old system's {t}: its {what} is not in Sales",
                                      connection_id=await h.conn(u.session, "RADIANT"), created_by_name=WHO)
                        await u.commit()
                continue
            n[f"{t}: read as it came"] += 1
            if not args.apply:
                continue
            async with uow_factory(SYSTEM_ACTOR) as u:
                await h.store(u.session, f, kind=kind, system="RADIANT", direction="IN", at=at, data=data, processing_status="Pending",
                              connection_id=await h.conn(u.session, "RADIANT"), created_by_name=WHO)
                await u.commit()
            await h.ships.process_945s()
            async with uow_factory(SYSTEM_ACTOR) as u:
                await h.cancel_writes(u.session)
                await u.commit()
            continue
        n[f"{t}: not a file this reads (left)"] += 1
    if args.apply:
        async with uow_factory(SYSTEM_ACTOR) as u:
            await oplog.write(u.session, "INFO", "repair", f"old system files read from {root.name}",
                              context={"outcome": dict(n), "would_be_tasks": dict(h.problems.raised)})
            await u.commit()
    return {"files": len(files), "outcome": dict(n), "would_be_tasks": dict(h.problems.raised)}


if __name__ == "__main__":
    script_main(main)
