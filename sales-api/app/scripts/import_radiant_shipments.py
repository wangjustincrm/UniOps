"""Once (user 2026-11-24): the orders of Radiant's shipment report (2024-06 … 2026-11-23) that
Sales does not hold, and the lots and tracking of the old Shopify orders that have no 945 here.

Nothing leaves: every order made here is Shipped, historical and Not Required (a historical order
is never sent: FR-OB-17, outbound.eligible_clause / qualifies), no document of a kind that is sent
is written, no outbox row, no task, no email, no Shopify write. The counts of 940s, outbox rows and
tasks are read before and after, and anything but "the same" stops the run before its commit.

The report (JSON on stdin: {"orders": [...], "expiry": {lot: date}}) gives, per Radiant order, its
ship-to, dates, carrier, tracking and rows (item, lot, quantities). Then, in one transaction:

1. An order the report has and Sales does not:
   - a retailer's (its address is a DC Sales knows, and it has a PO) → EDI_850, the partner of that DC;
   - the others (a name, "C0031") → MANUAL, partner Manual. "9451a" left after order 9451 had shipped in full: a MANUAL
     order naming it, its reason "Extra shipment". "9175a" is the only shipment of order 9175, "9138a" the rest of a
     short one: no order, the shipment is that order's.
   A retailer's order carries the unit price the report names for it (user 2026-11-24: NC's monthly price where it
   is sure), noted as estimated; a line that did not ship, and every other order, has none.
   Internal numbers from FIRST, in shipping order: below Zoho's 1,000,000 and above any Shopify number.
   A 945 that is here with no order is linked to it (its rows, its stock moves, its task); else a
   shipment is made from the report, with the order's own rows kept as its file.
2. An old Shopify order, Shipped, with no 945 here: the shipment is made from the report. Its
   quantities and status stay; the no-lot stock moves it had are replaced by the report's.

    python -m app.scripts.import_radiant_shipments < report.json            # dry run
    python -m app.scripts.import_radiant_shipments --apply < report.json    # needs automatic runs off
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.core.deps import uow_factory
from app.domain.row_matching import assign_rows
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.integrations.epms_tasks import shared_tasks_table
from app.models.config import Partner
from app.models.documents import Document, DocumentLink
from app.models.orders import Order, OrderLine
from app.models.records import OutboxRecord
from app.models.shipments import Shipment, ShipmentRow
from app.models.stock import CUSTOMER, RADIANT, TO_CUSTOMER, StockMove
from app.platform.actors import SYSTEM_ACTOR
from app.platform.commands import lock
from app.scripts.base import ScriptArgs, script_main
from app.scripts.import_zoho_history import refuse_unless_quiet
from app.services import oplog
from app.services.doc_links import link
from app.services.orders import order_ref
from app.services.problems import EDI_945_NO_ORDER, task_service
from app.services.stock import ship_moment

FIRST, LAST = 800_001, 899_999
TORONTO = ZoneInfo("America/Toronto")
MANUAL = "Manual"
NOTE_ORDER = "From Radiant's shipment report (2026-11-24)."
NOTE = f"{NOTE_ORDER} No prices: the report carries none."
SOURCE = "Radiant shipment report"
EXTRA = "Extra shipment"
ESTIMATED = "Estimated price from NC monthly sales, not an invoice."
RESHIP = re.compile(r"#?(\d{4})([A-Za-z])")


def postal(v: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def moment(v: str) -> datetime:
    return datetime.fromisoformat(v).replace(tzinfo=TORONTO).astimezone(timezone.utc)


def unambiguous(number: str) -> bool:
    """A source number that no 945's key means as another order's: a Shopify number (tier 3 of
    find_orders) or one of Zoho's / Sales' own."""
    base = number.lstrip("0")
    return not base.isdigit() or not (int(base) < 300_000 or 800_000 <= int(base) < 2_000_000)


def by_item(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Ordered and shipped EA per item. A split line repeats its ordered quantity on each row."""
    out: dict[str, dict[str, int]] = {}
    lines: dict[str, dict[str, list[dict]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        lines[r["item"]][r["line"]].append(r)
    for item, per in lines.items():
        shipped = sum(r["shipped"] for rs in per.values() for r in rs)
        each = [max(r["ordered"] for r in rs) for rs in per.values()]
        same_split = len(each) > 1 and len(set(each)) == 1 and shipped <= each[0]
        out[item] = {"ordered": max(each[0] if same_split else sum(each), shipped), "shipped": shipped}
    return out


class Import:
    def __init__(self, s, data: dict) -> None:
        self.s = s
        self.orders: list[dict] = data["orders"]
        self.expiry = {k: date.fromisoformat(v) for k, v in data["expiry"].items() if v}
        self.n: collections.Counter = collections.Counter()
        self.left: list[str] = []
        self.made: list[Order] = []

    async def load(self) -> None:
        s = self.s
        rows = (await s.execute(select(Order.id, Order.source_type, Order.partner_code, Order.source_order_number,
                                       Order.warehouse_order_number_received, Order.historical, Order.fulfillment_status,
                                       Order.source_reference))).all()
        self.shopify = {str(r.source_order_number).lstrip("#"): r for r in rows if r.source_type == "SHOPIFY"}
        self.retail_po = {(r.partner_code, str(r.source_order_number).lstrip("0")): r for r in rows if r.source_type == "EDI_850"}
        self.any_po = {str(r.source_order_number).lstrip("0") for r in rows if r.source_type != "SHOPIFY"}
        self.warehouse = {r.warehouse_order_number_received for r in rows if r.warehouse_order_number_received}
        self.has_945 = set((await s.execute(select(Shipment.order_id).where(Shipment.applied.is_(True)))).scalars())
        self.loose = {sh.order_key_received: sh for sh in (await s.execute(select(Shipment).where(
            Shipment.order_id.is_(None), Shipment.shadow.is_(False)))).scalars()}
        self.partners = {p.code: p for p in (await s.execute(select(Partner))).scalars()}
        self.dcs: dict[str, tuple[str, dict]] = {}
        for code, ship_to in (await s.execute(select(Order.partner_code, Order.ship_to).where(
                Order.source_type == "EDI_850", Order.ship_to.is_not(None)).order_by(Order.id))).all():
            key = postal(ship_to.get("zip"))
            if key and self.dcs.get(key, (code,))[0] != code:
                raise SystemExit(f"postal code {key} is a DC of two partners")
            if key:
                self.dcs[key] = (code, ship_to)
        self.returned_944 = set((await s.execute(select(StockMove.order_id).where(
            StockMove.source_type == "return_order"))).scalars())  # its return is in the ledger, from its RA 944
        taken = (await s.execute(select(func.count()).select_from(Order).where(Order.order_number.between(FIRST, LAST)))).scalar_one()
        if taken:
            raise SystemExit(f"{taken} orders already have a number from {FIRST}: this ran before")

    def known(self, o: dict) -> bool:
        k, po = o["key"], (o["po"] or "").lstrip("0")
        return (k in self.shopify or k in self.warehouse or (bool(po) and po in self.any_po))

    # ── writing ──────────────────────────────────────────────────────────────
    async def run(self) -> None:
        await self.load()
        self.by_key = {o["key"]: o for o in self.orders}
        self.orders.sort(key=lambda o: (o["shipped_at"], o["key"]))
        number = FIRST
        for o in self.orders:
            r = self.shopify.get(o["key"])
            if r is not None:
                if r.id in self.has_945:
                    self.n["shopify: has its 945, left"] += 1
                elif not (r.historical and r.fulfillment_status == F.SHIPPED.value):
                    self.n[f"shopify: {r.fulfillment_status}, left"] += 1
                    self.left.append(f"{r.source_reference}: {r.fulfillment_status}")
                else:
                    await self.ship_old(o, r.id)
            elif self.known(o):
                self.n["retail / manual: here already, left"] += 1
            elif (parent := await self.part_of(o)) is not None:
                self.n["a letter's order: its own shipment, or the rest of it"] += 1
                await self.ship_old(o, parent)
            else:
                await self.make(o, number)
                number += 1
        await self.s.flush()

    async def part_of(self, o: dict) -> int | None:
        """The Shopify order that "9175a" ships itself, or the rest of a short one (FR-OB-28: one trailing letter is the
        base order). None: what left after the order had shipped in full — its own order, marked EXTRA."""
        m = RESHIP.fullmatch(o["key"])
        p = self.shopify.get(m[1]) if m else None
        if p is None:
            return None
        before = self.by_key.get(m[1])
        if before is None:
            return None if p.id in self.has_945 else p.id
        ordered: collections.Counter = collections.Counter()
        for code, q in (await self.s.execute(select(OrderLine.item_code, OrderLine.qty_ea).where(
                OrderLine.order_id == p.id, OrderLine.removed.is_(False), OrderLine.item_code.is_not(None)))).all():
            ordered[code] += int(q or 0)
        for r in before["rows"]:
            ordered[r["item"]] -= r["shipped"]
        sent: collections.Counter = collections.Counter()
        for r in o["rows"]:
            sent[r["item"]] += r["shipped"]
        return p.id if all(q <= max(ordered[item], 0) for item, q in sent.items()) else None

    async def make(self, o: dict, number: int) -> None:
        s, key, po = self.s, o["key"], (o["po"] or "").strip()
        dc = self.dcs.get(postal(o["ship_to"]["zip"]))
        retail = dc is not None and bool(po)
        if retail and not unambiguous(po):
            raise SystemExit(f"{key}: its PO could be read as another order's number")
        if key.startswith("0000") and not retail:
            raise SystemExit(f"{key}: a retailer's order whose DC is not known")
        ship_to, partner, parent = o["ship_to"], self.partners[MANUAL], None
        notes = [NOTE, f"Radiant order {key}."]
        if retail:
            partner, ship_to = self.partners[dc[0]], dc[1]
            kind, source_id, source_no, ref = "EDI_850", po, po, order_ref(partner, po)
        else:
            kind, source_id = "MANUAL", f"RADIANT-{key}"
            source_no = key if unambiguous(key) else f"Radiant {key}"
            ref = order_ref(partner, str(number))
            if m := RESHIP.fullmatch(key):
                parent = self.shopify[m[1]].id
                notes.append(f"A later shipment of SHP-{m[1]}.")
        prices = {k: Decimal(v) for k, v in (o.get("prices") or {}).items()}  # per EA, only for the items that shipped
        if prices and not retail:
            raise SystemExit(f"{key}: a price on an order that is not a retailer's")
        if prices:
            notes = [NOTE_ORDER, f"Radiant order {key}.", ESTIMATED]
        order = Order(
            order_number=number, partner_code=partner.code, source_type=kind, source_order_id=source_id, source_order_number=source_no,
            source_reference=ref, parent_order_id=parent, manual_reason=EXTRA if parent else None, order_date=moment(o["ordered_at"]).astimezone(TORONTO).date(),
            ship_to=ship_to, notes=" ".join(notes), shipping_method="Purolator" if o["carrier"].startswith("PRL") else
            "LTL" if o["carrier"] == "WIFS" else None, currency="CAD", fulfillment_status=F.SHIPPED.value,
            invoice_status=Inv.NOT_REQUIRED.value, historical=True, shadow=False, first_received_at=moment(o["ordered_at"]),
            warehouse_order_number_received=key, version=1)
        s.add(order)
        await s.flush()
        self.made.append(order)
        lines: dict[str, OrderLine] = {}
        for i, (item, q) in enumerate(by_item(o["rows"]).items(), start=1):
            lines[item] = OrderLine(order_id=order.id, line_no=i, item_code=item, source={"item_code": item}, ordered_qty=q["ordered"],
                                    order_unit="EA", factor_ea=1, qty_ea=q["ordered"], shipped_ea=q["shipped"], unit_price=prices.get(item))
            s.add(lines[item])
        await s.flush()
        self.n["made: retailer" if retail else "made: reship" if parent else "made: manual"] += 1
        self.n["made: with an estimated price"] += bool(prices)
        self.n["made EA"] += sum(r["shipped"] for r in o["rows"])
        here = self.loose.get(key)
        if here is not None:
            await self.link_945(here, order, lines)
        else:
            await self.shipment(o, order.id, {item: [(ln.id, ln.qty_ea)] for item, ln in lines.items()})

    async def link_945(self, sh: Shipment, order: Order, lines: dict[str, OrderLine]) -> None:
        """The 945 that came with no order: its rows, its stock moves (they left for no order) and its task."""
        s = self.s
        await lock(s, Shipment, sh.id)  # before its rows
        sh.order_id, sh.applied = order.id, True
        ids = []
        for r in (await s.execute(select(ShipmentRow).where(ShipmentRow.shipment_id == sh.id))).scalars():
            r.order_line_id = lines[r.item_code].id
            ids.append(str(r.id))
        for m in (await s.execute(select(StockMove).where(StockMove.source_type == "shipment_row", StockMove.source_key.in_(ids)))).scalars():
            if (m.from_place, m.to_place, m.order_id) != (RADIANT, CUSTOMER, None):
                raise SystemExit(f"945 shipment {sh.id}: its stock moves are not those of a 945 with no order")
            m.to_place, m.order_id = TO_CUSTOMER, order.id
        await link(s, sh.document_id, "order", order.id)
        await task_service(lambda: False).resolve_problem(s, EDI_945_NO_ORDER, sh.id)
        self.n["945 with no order linked"] += 1

    async def ship_old(self, o: dict, order_id: int) -> None:
        s = self.s
        await lock(s, Order, order_id)  # before its lines
        lines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == order_id, OrderLine.removed.is_(False))
                                      .order_by(OrderLine.line_no))).scalars())
        items = {r["item"] for r in o["rows"]}
        slots: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
        for ln in lines:
            if ln.item_code:
                slots[ln.item_code].append((ln.id, int(ln.qty_ea or 0)))
        if len(lines) == 1 and len(items) == 1 and lines[0].item_code is None:
            slots[next(iter(items))] = [(lines[0].id, int(lines[0].qty_ea or 0))]
        sh = await self.shipment(o, order_id, slots)
        if order_id in self.returned_944:
            await self.delivered(sh)
        for r in o["rows"]:  # the rest of a short order: its line's figure, where a 945 gave one
            ln = next((x for x in lines if x.item_code == r["item"] and x.shipped_ea is not None and x.shipped_ea < (x.qty_ea or 0)), None)
            if ln is not None:
                ln.shipped_ea = min(ln.shipped_ea + r["shipped"], ln.qty_ea)
        gone = list((await s.execute(select(StockMove).where(StockMove.source_type == "order_line",
                                                             StockMove.order_id == order_id))).scalars())
        for m in gone:
            await s.delete(m)
        self.n["old Shopify order: shipment made"] += 1
        self.n["no-lot stock moves replaced"] += len(gone)

    async def delivered(self, sh: Shipment) -> None:
        """All of it was delivered; what came back is already in the ledger. Stock sync writes it once, so not again."""
        s = self.s
        await s.flush()
        at = ship_moment(sh.ship_date, sh.created_at)
        for r in (await s.execute(select(ShipmentRow).where(ShipmentRow.shipment_id == sh.id, ShipmentRow.qty_shipped_ea > 0))).scalars():
            s.add(StockMove(at=at, item_code=r.item_code, lot=r.lot, expiry=r.expiry, qty_ea=r.qty_shipped_ea, from_place=TO_CUSTOMER,
                            to_place=CUSTOMER, source_type="delivery", source_key=str(r.id), order_id=sh.order_id,
                            document_id=sh.document_id, created_by_name="Sales"))

    async def shipment(self, o: dict, order_id: int, slots: dict[str, list[tuple[int, int]]]) -> Shipment:
        s = self.s
        at = moment(o["shipped_at"])
        body = json.dumps({"source": SOURCE, **o}, ensure_ascii=False, indent=1, sort_keys=True).encode()
        doc = Document(direction="IN", origin="USER", system="RADIANT", kind="OTHER", remote_name=f"radiant-report-{o['key']}.json",
                       size=len(body), sha256=hashlib.sha256(body).hexdigest(), content=body, created_at=at,
                       created_by_name=SOURCE, processing_status="Processed")
        s.add(doc)
        await s.flush()
        s.add(DocumentLink(document_id=doc.id, entity_type="order", entity_id=order_id))
        tracking = o["tracking"]
        sh = Shipment(document_id=doc.id, set_index=1, order_id=order_id, order_key_received=o["key"],
                      ship_date=at.astimezone(TORONTO).date(), ship_date_source=None, carrier=o["carrier"], mode=o["mode"],
                      tracking=tracking or None, bol=o["key"] if not tracking else None, ship_to=o["ship_to"], applied=True,
                      shadow=False, shopify_status="Not needed", version=1)
        s.add(sh)
        await s.flush()
        links: dict[int, int] = {}
        for item in {r["item"] for r in o["rows"]}:
            mine = [(i, r["shipped"]) for i, r in enumerate(o["rows"]) if r["item"] == item]
            if slots.get(item):
                links |= assign_rows(mine, slots[item]).links
        for i, r in enumerate(o["rows"]):
            s.add(ShipmentRow(shipment_id=sh.id, lx_index=i + 1, w12_index=1, lx_number=str(i + 1), item_code=r["item"], lot=r["lot"],
                              expiry=self.expiry.get(r["lot"]) if r["lot"] else None, qty_ordered_ea=r["ordered"],
                              qty_shipped_ea=r["shipped"], weight=r["weight"], tracking=r["tracking"], order_line_id=links.get(i),
                              needs_review=False))
            self.n["rows"] += 1
            self.n["rows with no order line"] += links.get(i) is None
        return sh


async def outgoing(s) -> dict[str, int]:
    """What could ever leave: it must not change."""
    t = shared_tasks_table()
    return {
        "940 / 943 / 810 / emails": int(await s.scalar(select(func.count()).select_from(Document).where(Document.direction == "OUT"))),
        "outbox": int(await s.scalar(select(func.count()).select_from(OutboxRecord))),
        "tasks": int(await s.scalar(select(func.count()).select_from(t))),
        "next order number": int(await s.scalar(select(func.max(Order.order_number)))),
    }


async def main(args: ScriptArgs) -> dict:
    data = json.load(sys.stdin)
    if args.apply:
        await refuse_unless_quiet(uow_factory)
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        u.set_reason("Radiant's shipment report: the orders Sales does not hold (2026-11-24)")
        before = await outgoing(s)
        job = Import(s, data)
        await job.run()
        after = await outgoing(s)
        if before != after:
            raise SystemExit(f"something that can leave changed: {before} → {after}; nothing kept")
        bad = [o.id for o in job.made if not o.historical or o.fulfillment_status != F.SHIPPED.value
               or o.invoice_status != Inv.NOT_REQUIRED.value]
        if bad:
            raise SystemExit(f"{len(bad)} orders made are not historical / Shipped / Not Required; nothing kept")
        await oplog.write(s, "INFO", "repair", f"Radiant shipment report: {len(job.made)} orders made", context=dict(job.n))
        if args.apply:
            await u.commit()
        else:
            await s.rollback()
    return {"applied": args.apply, "unchanged": before, "counts": dict(job.n), "left": job.left[:20]}


if __name__ == "__main__":
    script_main(main)
