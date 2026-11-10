"""Old orders from Zoho's files (user 2026-11-10): Zoho's WorkDrive export taken as the real
files they were, stored, and run through Sales' own flow, so an old order has the same history
and files as a new one. Nothing leaves: no 940, 810, email, Shopify write or task.

    python -m app.scripts.import_zoho_history /path/to/export                   # dry run: what the export holds, per order
    python -m app.scripts.import_zoho_history /path/to/export --apply [--limit N]   # import (each order commits as it goes)

Each order commits on its own (the services it runs do), so a trial run is made on a throwaway
database with the production configuration, never on production itself.

The export folder holds Zoho's WorkDrive tree: Shopify/ (order pages), TrueCommerce/ (850, 810),
Radiant/ (940, 997, 945). Per order, in the order Zoho sent its 940:

1. its source — the Shopify page or the 850 — is stored as received and makes the order through
   OrderSync, at the time of the file (first received = when Zoho had it);
2. the order takes Zoho's internal number (Radiant's 945 refers to it), and Zoho's 940 is stored
   as the order's 940, sent by Zoho at the time of its file, with the values Sales locks on a 940
   of its own (the 945 checks compare with them);
3. Zoho's 997s and 945s are stored and read as Sales reads them (the order is accepted, shipped,
   its lots and parcels recorded); a Shopify write they queue is cancelled: Zoho made it;
4. a retailer order is confirmed delivered on Zoho's invoice date and invoiced; Zoho's 810 is
   stored as that invoice's file, sent by Zoho;
5. what Sales would have sent is built and compared with Zoho's file (940 and 810), and the
   finished order is marked historical (FR-OB-17): it never gets a 940 or an invoice again.

Refused unless the shadow run and automatic runs are both off (nothing else may act while it
runs). What would have been a task is listed in the report instead. Prints no customer data.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.domain.edi940 import build_940
from app.domain.intake import classify
from app.domain.shadow import compare
from app.domain.sources import from_shopify
from app.domain.status import FULFILLMENT, INVOICE, Ack, OutboundFile
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.integrations.shopify import ORDER_FIELDS, page_bytes, slim_customer, strip_card_data
from app.models.config import Connection, Partner
from app.models.documents import Document, DocumentLink
from app.models.invoices import InvoiceLine
from app.models.orders import Order
from app.models.records import OutboxRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.outbox import CANCELLED, FAILED, PENDING
from app.scripts.base import ScriptArgs, script_main
from app.services import oplog
from app.services.edi_intake import EdiIntake
from app.services.invoicing import Invoicing
from app.services.orders import OrderSync
from app.services.outbound import TORONTO, Outbound940
from app.services.settings import SCHEDULER_PAUSED, SETTINGS, SHADOW_MODE
from app.services.shipments import Shipments

STEP = "zoho history"
ZOHO = "ZOHO_MIGRATION"
TOR = ZoneInfo("America/Toronto")


# ── what would have been a task ──────────────────────────────────────────────

class ReportedProblems:
    """Stands in for the task service: a problem is counted for the report, never written."""

    def __init__(self) -> None:
        self.raised: collections.Counter = collections.Counter()
        self.current: str | None = None
        self.by_order: dict[str, list[str]] = collections.defaultdict(list)

    async def raise_problem(self, session, event: str, entity: object, task, **kw) -> None:
        self.raised[event] += 1
        if self.current:
            self.by_order[self.current].append(event)

    async def resolve_problem(self, session, event: str, entity: object, **kw) -> int:
        return 0


class NoBarcodes:
    """No Shopify call: a line without a SKU match goes to review, as it would without the barcode."""

    async def get(self, variant_id: str) -> tuple[str | None, str | None]:
        return None, "not read (Zoho history)"


# ── the export, indexed by order ─────────────────────────────────────────────

def file_time(name: str) -> datetime | None:
    m = re.search(r"(20\d{6})[_-](\d{6})", name)
    return datetime.strptime(m[1] + m[2], "%Y%m%d%H%M%S").replace(tzinfo=TOR) if m else None


def envelope_time(data: bytes) -> datetime | None:
    for seg in data.decode("latin-1").replace("\r", "").replace("\n", "").split("~"):
        e = seg.split("*")
        if e[0] == "GS" and len(e) > 5:
            return datetime.strptime(e[4] + (e[5] + "0000")[:4], "%Y%m%d%H%M").replace(tzinfo=TOR)
    return None


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes().decode("utf-8", "replace"), strict=False)


@dataclass
class Chain:
    """One order as Zoho's files tell it."""
    part: str                      # SHP, WM, LCL, SOB, OFG, JC (Zoho's file-name partner)
    zoho_number: int               # Zoho's internal order number (W05-02 of a retailer 940)
    source_number: str             # the Shopify order number or the PO
    sent_at: datetime
    f940: Path
    snapshots: list[dict[str, Any]] = field(default_factory=list)   # Shopify: the order in Zoho's pages, oldest first
    pages: list[Path] = field(default_factory=list)
    f850: Path | None = None
    f997: list[Path] = field(default_factory=list)
    f945: list[Path] = field(default_factory=list)
    f810: Path | None = None

    @property
    def retail(self) -> bool:
        return self.part != "SHP"

    @property
    def label(self) -> str:
        return f"{self.part}-{self.source_number}"


def index(root: Path) -> tuple[list[Chain], collections.Counter]:
    notes: collections.Counter = collections.Counter()
    snaps: dict[str, list[tuple[dict[str, Any], Path]]] = collections.defaultdict(list)
    for f in sorted((root / "Shopify").glob("*.json")):
        try:
            page = read_json(f)
        except ValueError:
            notes["shopify page not readable"] += 1
            continue
        for o in page.get("orders") or []:
            snaps[str(o.get("order_number"))].append((o, f))
    p850: dict[str, list[Path]] = collections.defaultdict(list)
    z810: dict[str, Path] = {}
    for f in sorted((root / "TrueCommerce").glob("*")):
        m = re.match(r"(850|810)-[A-Za-z]+-(?:\d+-)?([^-.]+)", f.name)
        if not m:
            continue
        if m[1] == "850":
            p850[m[2]].append(f)
        else:
            z810[m[2]] = f
    f997: dict[str, list[Path]] = collections.defaultdict(list)
    f945: dict[str, list[Path]] = collections.defaultdict(list)
    for f in sorted((root / "Radiant").glob("997-*")):
        f997[f.name.split("-")[2]].append(f)
    for f in sorted((root / "Radiant").glob("945-*")):
        p = f.name.split("-")
        if p[1] != "Radiant":
            f945[p[2]].append(f)
    chains: dict[int, Chain] = {}
    for f in sorted((root / "Radiant").glob("940-*")):
        p = f.name.split("-")
        part, number, source = p[1], p[2], re.sub(r"\.edi$", "", p[3].split("_")[0])
        data = f.read_bytes()
        at = file_time(f.name) or envelope_time(data)
        try:
            zn = int(number)
        except ValueError:
            notes["940 name without Zoho's number"] += 1
            continue
        if at is None:
            notes["940 without a time"] += 1
            continue
        if zn in chains:  # a resend by Zoho: the first is the one the warehouse acted on
            notes["940 sent again by Zoho"] += 1
            continue
        c = Chain(part, zn, source, at, f, f997=f997.get(number, []), f945=f945.get(number, []))
        if c.retail:
            files = sorted(p850.get(source, []), key=lambda x: ("OverwaiteaFood" in x.name, x.name))
            c.f850 = files[0] if files else None
            c.f810 = z810.get(source)
        else:
            found = sorted(snaps.get(source, []), key=lambda x: x[0].get("updated_at") or "")
            c.snapshots = [o for o, _ in found]
            c.pages = list(dict.fromkeys(pf for _, pf in found))
        chains[zn] = c
    return sorted(chains.values(), key=lambda c: c.sent_at), notes


def segment_tag(row: dict) -> str:
    """The segment a difference is in, without its values (they can be a customer's): "N1*BT", "W01"."""
    e = (row["zoho"] or row["sales"] or "").split("*")
    return e[0] + ("*" + e[1] if e[0] in ("N1", "N9", "G62", "PER") and len(e) > 1 else "")


# ── one order ────────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    label: str
    zoho_number: int
    result: str = "imported"          # imported | skipped: <why> | failed: <type>
    status: str | None = None         # the order's Status at the end
    compare_940: str | None = None    # same | differs: <segments> | not built: <why>
    compare_810: str | None = None
    problems: list[str] = field(default_factory=list)


class History:
    def __init__(self, uow_factory, root: Path) -> None:
        self.uow_factory = uow_factory
        self.root = root
        self.now = datetime.now(timezone.utc)
        clock = lambda: self.now  # noqa: E731 — every service runs at the file's time
        self.problems = ReportedProblems()
        self.outbound = Outbound940(uow_factory=uow_factory, tasks=self.problems, sftp=None, clock=clock)
        self.ships = Shipments(uow_factory=uow_factory, tasks=self.problems, shopify=None, outbound=self.outbound, clock=clock)
        self.invoicing = Invoicing(uow_factory=uow_factory, tasks=self.problems, sftp=None, email=None, clock=clock)
        self.intake = EdiIntake(uow_factory=uow_factory, tasks=self.problems, clock=clock)
        self.stored: dict[str, int] = {}   # sha256 → document id (a Shopify page holds many orders)

    def at(self, t: datetime) -> None:
        self.now = t.astimezone(timezone.utc)

    async def store(self, s, path: Path | None, *, kind: str, system: str, direction: str, at: datetime,
                    data: bytes | None = None, origin: str = "PARTNER", **values: Any) -> Document:
        data = data if data is not None else path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha in self.stored:
            return await s.get(Document, self.stored[sha])
        found = (await s.execute(select(Document).where(Document.sha256 == sha, Document.content.is_not(None)))).scalar_one_or_none()
        if found is not None:
            self.stored[sha] = found.id
            return found
        doc = Document(direction=direction, origin=origin, system=system, kind=kind, remote_name=(path.name if path else kind)[:500],
                       size=len(data), sha256=sha, content=data, created_at=at.astimezone(timezone.utc),
                       created_by_name="Zoho history", **values)
        s.add(doc)
        await s.flush()
        self.stored[sha] = doc.id
        return doc

    async def link(self, s, doc: Document, order_id: int) -> None:
        if (await s.execute(select(DocumentLink.id).where(DocumentLink.document_id == doc.id, DocumentLink.entity_type == "order",
                                                         DocumentLink.entity_id == order_id))).first() is None:
            s.add(DocumentLink(document_id=doc.id, entity_type="order", entity_id=order_id))

    async def cancel_writes(self, s) -> None:
        """The Shopify fulfilment and close a 945 queues: Zoho made them."""
        await s.execute(update(OutboxRecord).where(OutboxRecord.status.in_((PENDING, FAILED)))
                        .values(status=CANCELLED, last_error="Zoho history: done by Zoho"))

    async def source(self, c: Chain, out: Outcome) -> int | None:
        """The order from its source, at the time Zoho had it."""
        received = c.sent_at - timedelta(minutes=5)
        self.at(received)
        if c.retail:
            if c.f850 is None:
                out.result = "skipped: no 850 in the export"
                return None
            async with self.uow_factory(SYSTEM_ACTOR) as u:
                await self.store(u.session, c.f850, kind="850_JSON", system="TRUECOMMERCE", direction="IN", at=received,
                                 processing_status="Pending", connection_id=await self.conn(u.session, "SFTP"))
                await u.commit()
            await self.intake.run_pass()
            async with self.uow_factory(SYSTEM_ACTOR) as u:
                o = (await u.session.execute(select(Order).where(Order.source_type == "EDI_850",
                                                                 Order.source_order_number == c.source_number))).scalars().first()
                return o.id if o else None
        if not c.snapshots:
            out.result = "skipped: the order is not in Zoho's Shopify pages"
            return None
        first = slim_customer({k: v for k, v in c.snapshots[0].items() if k in ORDER_FIELDS})
        async with self.uow_factory(SYSTEM_ACTOR) as u:
            s = u.session
            conn = (await s.execute(select(Connection).where(Connection.kind == "SHOPIFY"))).scalars().first()
            abbrev = (await s.execute(select(Partner.abbrev).where(Partner.code == conn.partner_code))).scalar_one()
            docs = []
            for pf in c.pages[:1]:
                page = strip_card_data(read_json(pf))   # never stored with card data
                docs.append(await self.store(s, pf, kind="SHOPIFY_ORDER_JSON", system="SHOPIFY", direction="IN",
                                             at=file_time(pf.name) or received, data=page_bytes(page),
                                             processing_status="Processed", connection_id=conn.id))
            order = await OrderSync(u, self.problems).upsert(
                from_shopify(first, partner_code=conn.partner_code, partner_abbrev=abbrev, cutoff=None),
                connection_id=conn.id, document_id=docs[0].id if docs else None, received_at=received, barcodes=NoBarcodes())
            await u.commit()
            return order.id

    async def conn(self, s, kind: str) -> int | None:
        return (await s.execute(select(Connection.id).where(Connection.kind == kind).order_by(Connection.id))).scalars().first()

    async def zoho_940(self, c: Chain, order_id: int, out: Outcome) -> bool:
        """Zoho's 940 as the order's 940, sent at its file's time; Sales' own build compared with it."""
        data = c.f940.read_bytes()
        self.at(c.sent_at)
        async with self.uow_factory(SYSTEM_ACTOR) as u:
            s = u.session
            order = await s.get(Order, order_id, with_for_update=True)
            clash = (await s.execute(select(Order.id).where(Order.order_number == c.zoho_number, Order.id != order.id))).first()
            if clash is None:
                order.order_number = c.zoho_number   # the 945 refers to it (W06-02)
            else:
                out.problems.append("zoho_number_taken")
            partner = (await s.execute(select(Partner).where(Partner.code == order.partner_code))).scalar_one()
            snap = await self.outbound.snapshot(s, order, partner)
            built = build_940(snap, 0, c.sent_at.astimezone(TORONTO))
            diff = compare(data, built.content)
            out.compare_940 = "same" if diff.differences == 0 else "differs: " + ",".join(sorted({segment_tag(r) for r in diff.rows if not r["same"]}))
            k = classify(data)
            doc = await self.store(s, c.f940, kind="940_EDI", system="RADIANT", direction="OUT", at=c.sent_at, data=data,
                                   origin=ZOHO, file_status=OutboundFile.SENT.value, ack_status=Ack.WAITING.value,
                                   sent_at=c.sent_at.astimezone(timezone.utc), isa13=k.isa13, gs06=k.gs06, st02=k.st02,
                                   partner_code=order.partner_code, connection_id=await self.conn(s, "SFTP"),
                                   body_json={"order_id": order.id, "lines": built.lines_sent, "locked": built.locked,
                                              "reason": "sent by Zoho (history)"})
            await self.link(s, doc, order.id)
            moved = True
            if F(order.fulfillment_status) in (F.ARCHIVE_SUPPRESSED, F.REVIEW, F.PAYMENT_HOLD):
                # Zoho's page was read after the order changed (archived, or a line Sales cannot
                # match without Shopify): Zoho sent the 940 all the same, so the order went.
                out.problems.append(f"zoho_sent_from_{order.fulfillment_status}")
                FULFILLMENT.transition(order, F.PENDING, actor=u.actor, events=u.events,
                                       reason="Zoho sent its 940 (history)")
            for to in (F.GENERATED_940, F.SENT_940):
                if FULFILLMENT.can(F(order.fulfillment_status), to):
                    FULFILLMENT.transition(order, to, actor=u.actor, events=u.events, reason="940 sent by Zoho (history)")
                elif F(order.fulfillment_status) != to:
                    moved = False
            if not moved:
                out.problems.append(f"940_not_applied_from_{order.fulfillment_status}")
            await u.commit()
            return moved

    async def acks_and_shipments(self, c: Chain) -> datetime | None:
        last = None
        for f in c.f997:
            data = f.read_bytes()
            t = file_time(f.name) or envelope_time(data) or c.sent_at
            self.at(t)
            async with self.uow_factory(SYSTEM_ACTOR) as u:
                await self.store(u.session, f, kind="997_EDI", system="RADIANT", direction="IN", at=t, data=data,
                                 processing_status="Pending", connection_id=await self.conn(u.session, "SFTP"))
                await u.commit()
            await self.outbound.process_997s()
        for f in c.f945:
            data = f.read_bytes()
            t = file_time(f.name) or envelope_time(data) or c.sent_at
            self.at(t)
            last = t
            async with self.uow_factory(SYSTEM_ACTOR) as u:
                await self.store(u.session, f, kind="945_EDI", system="RADIANT", direction="IN", at=t, data=data,
                                 processing_status="Pending", connection_id=await self.conn(u.session, "SFTP"))
                await u.commit()
            await self.ships.process_945s()
            async with self.uow_factory(SYSTEM_ACTOR) as u:
                await self.cancel_writes(u.session)
                await u.commit()
        return last

    async def invoice(self, c: Chain, order_id: int, shipped_at: datetime | None, out: Outcome) -> None:
        """Delivery confirmed on Zoho's invoice date; Zoho's 810 is the invoice's file, sent by Zoho."""
        z = read_json(c.f810)["Invoice"][0] if c.f810 else None
        when = date.fromisoformat(z["InvoiceDate"]) if z else None
        if when is None:
            out.problems.append("no_zoho_810")
            return
        t = datetime.combine(when, datetime.min.time()).replace(tzinfo=TOR) + timedelta(hours=12)
        self.at(max(t, (shipped_at or t) + timedelta(hours=1)))
        await self.invoicing.advance()
        async with self.uow_factory(SYSTEM_ACTOR) as u:
            s = u.session
            order = await s.get(Order, order_id, with_for_update=True)
            if order.invoice_status == Inv.AWAITING_REQUESTED_DATE.value:  # Zoho invoiced before the requested date
                out.problems.append("zoho_invoiced_before_requested_date")
                INVOICE.transition(order, Inv.WAITING_FOR_DELIVERY_CONFIRMATION, actor=u.actor, events=u.events,
                                   reason=f"Zoho invoiced on {when} (history)")
                await s.flush()
            if order.invoice_status != Inv.WAITING_FOR_DELIVERY_CONFIRMATION.value:
                out.problems.append(f"invoice_from_{order.invoice_status}")
                return
            inv = await self.invoicing.confirm(u, order_id, confirmed_date=when, note="Zoho history: delivered per Zoho's invoice")
            await s.flush()
            order = await s.get(Order, order_id, with_for_update=True)
            if order.invoice_status != Inv.READY.value:
                out.problems.append(f"invoice_{order.invoice_status}")
                await u.commit()
                return
            lines = list((await s.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
                                          .order_by(InvoiceLine.line_no))).scalars())
            if inv.method == "810_JSON":
                ours = json.loads(await self.invoicing._810(s, inv, order, lines))["Invoice"][0]
                out.compare_810 = "same" if ours == z else "differs: " + ",".join(sorted(k for k in set(ours) | set(z) if ours.get(k) != z.get(k)))[:200]
                doc = await self.store(s, c.f810, kind="810_JSON", system="TRUECOMMERCE", direction="OUT", at=t, origin=ZOHO,
                                       file_status=OutboundFile.SENT.value, ack_status=Ack.NOT_REQUIRED.value,
                                       sent_at=t.astimezone(timezone.utc), partner_code=order.partner_code)
                await self.link(s, doc, order.id)
                inv.document_id = doc.id
            inv.sent_at, inv.send_count = t.astimezone(timezone.utc), 1
            INVOICE.transition(order, Inv.SENT, actor=u.actor, events=u.events, reason="invoice sent by Zoho (history)")
            await u.commit()

    async def finish(self, order_id: int, out: Outcome) -> None:
        async with self.uow_factory(SYSTEM_ACTOR) as u:
            s = u.session
            o = await s.get(Order, order_id, with_for_update=True)
            done = o.fulfillment_status in (F.SHIPPED.value, F.CANCELLED.value) and o.invoice_status in (
                Inv.SENT.value, Inv.NOT_REQUIRED.value)
            if done or o.fulfillment_status == F.CANCELLED.value:
                o.historical = True
            out.status = f"{o.fulfillment_status} / {o.invoice_status}" + (" (historical)" if o.historical else " (open)")
            await self.cancel_writes(s)
            await u.commit()

    async def one(self, c: Chain) -> Outcome:
        out = Outcome(c.label, c.zoho_number)
        self.problems.current = c.label
        order_id = await self.source(c, out)
        if order_id is None:
            if out.result == "imported":
                out.result = "skipped: no order was made from the source"
            return out
        if not await self.zoho_940(c, order_id, out):
            await self.finish(order_id, out)
            return out
        shipped = await self.acks_and_shipments(c)
        if c.retail and c.f945:
            await self.invoice(c, order_id, shipped, out)
        await self.finish(order_id, out)
        out.problems += self.problems.by_order.pop(c.label, [])
        return out


# ── the run ──────────────────────────────────────────────────────────────────

async def refuse_unless_quiet(uow_factory) -> None:
    async with uow_factory(SYSTEM_ACTOR) as u:
        if await SETTINGS.get(u.session, SHADOW_MODE):
            raise SystemExit("the shadow run is on: turn it off first (nothing would be applied)")
        if not await SETTINGS.get(u.session, SCHEDULER_PAUSED):
            raise SystemExit("automatic runs are on: turn them off first (nothing else may act while this runs)")


async def run(uow_factory, root: Path, *, apply: bool, limit: int | None = None) -> dict[str, Any]:
    await refuse_unless_quiet(uow_factory)
    chains, notes = index(root)
    if limit:
        chains = chains[:limit]
    h = History(uow_factory, root)
    results: list[Outcome] = []
    for c in chains:
        try:
            results.append(await h.one(c))
        except Exception as e:  # noqa: BLE001 — one order never stops the others; its text could carry a value
            results.append(Outcome(c.label, c.zoho_number, result=f"failed: {type(e).__name__}"))
    report = {
        "orders": len(chains), "export_notes": dict(notes),
        "result": dict(collections.Counter(r.result for r in results)),
        "status": dict(collections.Counter(r.status for r in results if r.status)),
        "940": dict(collections.Counter((r.compare_940 or "none").split(":")[0] for r in results)),
        "810": dict(collections.Counter((r.compare_810 or "none").split(":")[0] for r in results)),
        "would_be_tasks": dict(h.problems.raised),
        "problems": dict(collections.Counter(p for r in results for p in r.problems)),
        "orders_detail": [vars(r) for r in results if r.result != "imported" or r.problems or (r.compare_940 or "same") != "same"
                          or (r.compare_810 or "same") != "same" or "(open)" in (r.status or "")],
    }
    if apply:
        async with uow_factory(SYSTEM_ACTOR) as u:
            await oplog.write(u.session, "INFO", STEP, f"Zoho history: {len(chains)} orders read",
                              context={k: v for k, v in report.items() if k != "orders_detail"})
            await u.commit()
    return report


async def _main(args: ScriptArgs) -> dict[str, Any]:
    from app.core.deps import uow_factory
    root = Path(args.positional[0])
    limit = int(args.value("limit")) if args.value("limit") else None
    if args.apply:
        return await run(uow_factory, root, apply=True, limit=limit)
    chains, notes = index(root)
    return {"orders": len(chains), "export_notes": dict(notes),
            "by_partner": dict(collections.Counter(c.part for c in chains)),
            "without_source": sum(1 for c in chains if (c.retail and c.f850 is None) or (not c.retail and not c.snapshots)),
            "without_945": sum(1 for c in chains if not c.f945),
            "retail_without_810": sum(1 for c in chains if c.retail and c.f945 and c.f810 is None),
            "from": min(c.sent_at for c in chains).isoformat() if chains else None,
            "to": max(c.sent_at for c in chains).isoformat() if chains else None}


if __name__ == "__main__":
    script_main(_main, takes_values=("limit",))
