"""945s → shipments (FR-OB-25 … 25d, FR-OB-28; P5) and the Shopify
write-back (FR-OB-11 / 11a).

A 945 is first *planned* — its order (FR-OB-28), each W12's order line
(FR-OB-25a), the totals per line, the FR-OB-25d checks — and then either
*applied* (shipment + rows, the order Shipped, tasks, the Shopify outbox row)
or, during the shadow run, only recorded as what Sales would have done
(`sales_shadow_matches`, app/services/shadow.py). Never both.

- Idempotent: a shipment is (document, set index); rows are (shipment, LX
  position, W12 position) — never the tracking number (FR-OB-25). Identical
  bytes under another name never get here (intake: Duplicate, FR-OB-71).
- One 945 per order (D18): the first closes the fulfilment (Shipped); a short
  shipment raises a task, no second 940. A second, different 945 is stored
  with `applied = false`, the order goes On Hold with a task.
- The checks (FR-OB-25d) only notify: recorded on the shipment and logged as
  "945 validation" (P8 routes them). The exceptions the requirements name are
  tasks: an unmatched line (review, FR-OB-25a), no order (FR-OB-25c).
- Shopify (FR-OB-11 / 11a): a shipment of a Shopify order queues ONE outbox
  row: read the order's fulfillments first (a retry never duplicates); add the
  tracking to an open fulfillment created by Zoho (AC-OB-72), else fulfill the
  open fulfillment orders with the shipped quantities and the tracking; then a
  second row closes (archives) the order. Each is made once; a failure raises
  a task and is retried only by a person. Never during the shadow run, never
  for a shadow shipment (pre_check — the second barrier).
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, select

from app.domain.edi945 import OrderKey, Shipment945, order_key, parse_945
from app.domain.orders import FROM_945
from app.domain.status import FULFILLMENT, INBOUND_FILE, Ack, InboundFile
from app.domain.status import Fulfillment as F
from app.domain.x12 import X12Error
from app.integrations.shopify import ShopifyForbidden, readable as shopify_readable
from app.models.config import Connection, Item, Partner
from app.models.documents import Document, DocumentLink
from app.models.orders import Order, OrderLine
from app.models.records import OutboxRecord
from app.models.shipments import Shipment, ShipmentRow
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.outbox import ONCE, Outbox, OutboxKind
from app.platform.pipeline import isolated
from app.services import oplog
from app.services.intake import ConnectionSnapshot
from app.services.order_from_945 import SYNTHETIC_PREFIX, attach, warehouse_number_is
from app.services.outbound import TORONTO, latest_940
from app.services.problems import (
    EDI_945_LINE_UNMATCHED, EDI_945_NO_ORDER, EDI_945_VALIDATION, ORDER_FILE_UNREADABLE, SECOND_945,
    SHOPIFY_WRITE_FAILED, SHORT_SHIPMENT, problem, send_refused,
)
from app.services.settings import SETTINGS, SHADOW_MODE

logger = logging.getLogger(__name__)

STEP = "945"
FULFILL, CLOSE = "shopify_fulfill", "shopify_close"
OUTBOX = Outbox(OutboxRecord)
SHIPPABLE = (F.SENT_940, F.ACCEPTED_940, F.TIMEOUT_940)
TRACKING_COMPANY = "Purolator"  # FR-OB-11: Shopify orders ship with Purolator


@dataclass
class RowPlan:
    lx_index: int
    w12_index: int
    order_line_id: int | None


@dataclass
class Plan:
    """What applying one 945 set does — computed, not yet written."""
    key: OrderKey
    order_id: int | None = None
    order_number: int | None = None
    match_note: str | None = None          # why no order (none / several / two letters)
    second_of: list[str] = field(default_factory=list)   # the earlier 945s' file names
    rows: list[RowPlan] = field(default_factory=list)
    lines: dict[str, dict[str, Any]] = field(default_factory=dict)  # line id → {item, ordered, shipped}
    short: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    status_to: str | None = None           # the fulfillment status it moves to
    accept_940: bool = False               # Accepted by 945 (no 997 yet)

    def summary(self) -> dict[str, Any]:
        return {"order_id": self.order_id, "order_number": self.order_number, "match": self.match_note,
                "second_945_of": self.second_of, "status_to": self.status_to, "accepted_by_945": self.accept_940,
                "short": self.short, "unmatched": self.unmatched, "checks": self.checks, "lines": self.lines}


@dataclass
class ShipmentReport:
    files: int = 0
    shipments: int = 0
    no_order: int = 0
    second: int = 0
    shadow: int = 0
    failed: int = 0
    detail: list[str] = field(default_factory=list)


def _check(kind: str, message: str, **values: Any) -> dict[str, Any]:
    return {"check": kind, "message": message, **{k: (str(v) if isinstance(v, (date, datetime)) else v) for k, v in values.items()}}


class Shipments:
    def __init__(self, *, uow_factory, tasks, shopify, outbound, shadow=None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.uow_factory = uow_factory
        self.tasks = tasks
        self.shopify = shopify
        self.outbound = outbound
        self.shadow = shadow  # app.services.shadow.ShadowRun — the shadow-run matcher
        self.clock = clock

    # ── the pass ─────────────────────────────────────────────────────────────
    async def process_945s(self, *, run_id: int | None = None, connection_id: int | None = None) -> ShipmentReport:
        report = ShipmentReport()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            ids = list((await uow.session.execute(select(Document.id).where(
                Document.kind == "945_EDI", Document.direction == "IN",
                Document.processing_status == InboundFile.PENDING.value,
                *([Document.connection_id == connection_id] if connection_id is not None else []))
                .order_by(Document.id))).scalars())
        for did in ids:
            report.files += 1
            outcome = await isolated(f"945 document {did}", lambda uow, did=did: self._one(uow, did, report, run_id),
                                     uow_factory=self.uow_factory, log=self._failed(did, run_id))
            if not outcome.ok:
                report.failed += 1
                report.detail.append(f"document {did}: {outcome.message}")
        return report

    def _failed(self, did: int, run_id):
        async def log(o) -> None:
            await oplog.write_alone(self.uow_factory, "ERROR", STEP, f"945 document {did}: {o.error_type}: {o.message}",
                                    run_id=run_id, document_id=did)
        return log

    async def _one(self, uow, did: int, report: ShipmentReport, run_id) -> str:
        s = uow.session
        doc = await s.get(Document, did, with_for_update=True)
        INBOUND_FILE.transition(doc, InboundFile.PROCESSING, actor=uow.actor, events=uow.events, reason="reading the 945")
        content = (await s.execute(select(Document.content).where(Document.id == did))).scalar_one()
        try:
            sets = parse_945(content)
        except X12Error as e:
            doc.error = str(e)
            INBOUND_FILE.transition(doc, InboundFile.FAILED, actor=uow.actor, events=uow.events, reason=str(e))
            await self.tasks.raise_problem(s, ORDER_FILE_UNREADABLE, doc.id, problem(  # raised in the shadow run too
                ORDER_FILE_UNREADABLE, f"945 could not be read: {doc.remote_name}"[:255],
                f"945 file {doc.remote_name} (document {doc.id}) was stored but cannot be read: {e}."))
            return "failed"
        if doc.shadow or await SETTINGS.get(s, SHADOW_MODE):
            # §5.4: it answers a Zoho 940. Kept and compared, never applied.
            doc.shadow = True
            plans = [await self.plan(s, doc, sh) for sh in sets]
            for i, (sh, p) in enumerate(zip(sets, plans), start=1):
                shipment = await self._store(s, doc, i, sh, p, applied=False, shadow=True)
                order = await s.get(Order, p.order_id) if p.order_id else None
                if order is not None and order.source_type == FROM_945:  # a later part of its warehouse order
                    await attach(s, order, shipment)
            if self.shadow is not None:
                await self.shadow.match_945(uow, doc, sets, plans)
            doc.error = "shadow run: stored and compared, not applied"
            INBOUND_FILE.transition(doc, InboundFile.SKIPPED, actor=uow.actor, events=uow.events, reason="shadow run")
            await oplog.write(s, "INFO", STEP, f"shadow run: 945 {doc.remote_name} compared, not applied",
                              run_id=run_id, document_id=doc.id)
            report.shadow += 1
            return "shadow"
        for i, sh in enumerate(sets, start=1):
            await self.apply(uow, doc, i, sh, report, run_id=run_id)
        INBOUND_FILE.transition(doc, InboundFile.PROCESSED, actor=uow.actor, events=uow.events, reason="945 applied")
        return "applied"

    # ── planning (no writes) ─────────────────────────────────────────────────
    async def find_orders(self, s, key: OrderKey) -> list[Order]:
        """FR-OB-28: Shopify order id, then internal number, then source number;
        the first that finds any decides (one = the order, several = review)."""
        if key.base is None:
            return []
        tiers = []
        if key.numeric:
            tiers.append(select(Order).where(Order.source_type == "SHOPIFY", Order.source_order_id == key.base))
            tiers.append(select(Order).where(Order.order_number == int(key.base)))
            tiers.append(select(Order).where(
                func.nullif(func.ltrim(func.ltrim(Order.source_order_number, "#"), "0"), "") == key.base))
        else:
            tiers.append(select(Order).where(Order.source_order_number == key.base))
        # An order made from an earlier part of this warehouse order (FR-OB-25c).
        tiers.append(select(Order).where(Order.source_type == FROM_945, warehouse_number_is(key)))
        for q in tiers:
            found = list((await s.execute(q.order_by(Order.id))).scalars())
            if found:
                return found
        return []

    async def plan(self, s, doc: Document, sh: Shipment945) -> Plan:
        key = order_key(sh.order_key_raw or "")
        p = Plan(key=key)
        orders = await self.find_orders(s, key)
        if not orders and doc.shadow and sh.po:
            # The shadow run: W06-02 is Zoho's own order number (e.g. "001008597"), which Sales
            # does not know. The PO (W06-06 / N9*PO) is then tried as the order's source number
            # (real files, 2026-11-03). A key with neither (a Zoho manual order, "C0031") stays
            # unmatched until Zoho's reference 940 maps it (its W05-02 / W05-03).
            by_po = list((await s.execute(select(Order).where(
                func.nullif(func.ltrim(Order.source_order_number, "0"), "") == sh.po.strip().lstrip("0")).order_by(Order.id))).scalars())
            if len(by_po) == 1:
                orders = by_po
                p.match_note = f"matched by its PO {sh.po} (shadow run: {sh.order_key_raw} is Zoho's number)"
        if len(orders) != 1:
            p.match_note = ("two trailing letters or no number" if key.base is None
                            else f"{len(orders)} orders match {key.base}" if orders else f"no order matches {key.base}")
            p.rows = [RowPlan(r.lx_index, r.w12_index, None) for r in sh.rows]
            return p
        order = orders[0]
        p.order_id, p.order_number = order.id, order.order_number
        earlier = (await s.execute(select(Document.remote_name).join(Shipment, Shipment.document_id == Document.id).where(
            Shipment.order_id == order.id, Shipment.applied.is_(True), Shipment.document_id != doc.id))).scalars().all()
        if earlier:
            p.second_of = [n or "?" for n in earlier]
            p.status_to = F.ON_HOLD.value if FULFILLMENT.can(F(order.fulfillment_status), F.ON_HOLD) else None
            p.checks.append(_check("order state", "a second, different 945 for an order already shipped: not applied",
                                   found=sh.order_key_raw, expected="one 945 per order"))
            p.rows = [RowPlan(r.lx_index, r.w12_index, None) for r in sh.rows]
            return p
        lines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == order.id,
                                                              OrderLine.removed.is_(False)).order_by(OrderLine.line_no))).scalars())
        items = {i.item_code: i for i in (await s.execute(select(Item).where(
            Item.item_code.in_([ln.item_code for ln in lines if ln.item_code])))).scalars()}
        sent = await latest_940(s, order.id)
        locked = ((sent.body_json or {}).get("locked") or {}) if sent is not None else {}
        lx_of = {str(x["line_no"]): str(x["lx"]) for x in ((sent.body_json or {}).get("lines") or [])} if sent else {}
        ordered = {ln.id: int((locked.get("lines") or {}).get(str(ln.line_no), {}).get("qty_ea") or ln.qty_ea or 0) for ln in lines}

        def ids_of(ln: OrderLine) -> set[str]:
            src = ln.source or {}
            item = items.get(ln.item_code)
            vals = {ln.item_code, src.get("buyer_part"), src.get("upc"), src.get("gtin"),
                    item.upc if item else None, item.gtin if item else None}
            return {str(v).strip().lstrip("0") for v in vals if v}

        # Several lines of the same item (a Shopify order's "Trial Can" and "Can", both CF0102):
        # what a W12 shipped fills them up to each line's quantity — the line whose 940 LX is
        # the W12's LX first, then in line order — and only what is left over is over-shipped.
        # (In Radiant's real 945s an LX is a pallet, so its number rarely names a 940 line.)
        # A shadow-run 945 may be one part of a split shipment (Radiant / Zoho: "9451a", "9451b"):
        # what the earlier parts of this order shipped (their rows linked to the lines) counts first,
        # so the parts add up. Never for an applied 945 (one 945 per order: D18).
        shipped: dict[int, int] = await self._shadow_shipped(s, order.id, doc.id) if doc.shadow else {}
        for r in sh.rows:
            ident = {str(v).strip().lstrip("0") for v in (r.item_code, r.upc) if v}
            cands = [ln for ln in lines if ident & ids_of(ln)]
            if len(cands) > 1 and r.lx_number:
                lx = str(r.lx_number).lstrip("0")
                cands = sorted(cands, key=lambda ln: lx_of.get(str(ln.line_no)) != lx)  # stable: line order otherwise
            if not cands:
                p.rows.append(RowPlan(r.lx_index, r.w12_index, None))
                p.unmatched.append(f"LX {r.lx_number or r.lx_index} W12 {r.item_code or r.upc}: {r.qty_shipped} EA")
                p.checks.append(_check("extra or substituted item", f"{r.item_code or r.upc} was not on the 940",
                                       found=r.item_code or r.upc, expected=None))
                continue
            left, first = r.qty_shipped, None
            for ln in cands:
                take = min(left, max(ordered.get(ln.id, 0) - shipped.get(ln.id, 0), 0))
                if take > 0:
                    shipped[ln.id] = shipped.get(ln.id, 0) + take
                    left -= take
                    first = first or ln
                if left == 0:
                    break
            if left > 0:  # more than every candidate line ordered: over-shipped on the first
                shipped[cands[0].id] = shipped.get(cands[0].id, 0) + left
                first = first or cands[0]
            p.rows.append(RowPlan(r.lx_index, r.w12_index, first.id))
        for ln in lines:
            if not ln.item_code or not (items.get(ln.item_code) and items[ln.item_code].warehouse_held):
                continue
            want, got = ordered.get(ln.id, 0), shipped.get(ln.id, 0)
            p.lines[str(ln.id)] = {"line_no": ln.line_no, "item_code": ln.item_code, "ordered_ea": want, "shipped_ea": got}
            if got < want:
                p.short.append(f"{ln.item_code}: {want - got} EA not shipped ({got} of {want})")
            elif got > want:
                p.checks.append(_check("over-shipment", f"{ln.item_code}: shipped {got} EA against {want} on the 940",
                                       line=ln.line_no, found=got, expected=want))
        await self._checks(s, order, sh, sent, locked, p, shadow=doc.shadow)
        status = F(order.fulfillment_status)
        if status in SHIPPABLE:
            p.status_to = F.SHIPPED.value
        if sent is not None and sent.ack_status in (Ack.WAITING.value, Ack.TIMEOUT.value):
            p.accept_940 = True
        return p

    async def _checks(self, s, order: Order, sh: Shipment945, sent: Document | None, locked: dict, p: Plan, *,
                      shadow: bool = False) -> None:
        """FR-OB-25d (3) – (6); (1) and (2) are found with the lines."""
        partner = (await s.execute(select(Partner).where(Partner.code == order.partner_code))).scalar_one_or_none()
        ship = sh.ship_date
        for item, lot, exp in ((r.item_code, r.lot, r.expiry) for r in sh.rows):
            if ship is None or exp is None:
                continue
            if exp <= ship:
                p.checks.append(_check("expiry", f"lot {lot} of {item} expired on {exp} (ship date {ship})",
                                       found=exp, expected=f"after {ship}"))
            elif partner is not None and partner.min_shelf_life_days and (exp - ship).days < partner.min_shelf_life_days:
                p.checks.append(_check("expiry", f"lot {lot} of {item}: {(exp - ship).days} days left at shipping, "
                                       f"the partner wants {partner.min_shelf_life_days}",
                                       found=(exp - ship).days, expected=partner.min_shelf_life_days))
        status = F(order.fulfillment_status)
        if status == F.CANCELLED:
            p.checks.append(_check("order state", "the order is cancelled", found=status.value, expected="not cancelled"))
        if sent is None:
            if not shadow:  # the shadow run: Zoho sent the 940 — compared on Shadow compare instead
                p.checks.append(_check("order state", "no 940 was sent by Sales for this order", found=None,
                                       expected="a sent 940"))
        elif sent.ack_status not in (Ack.ACCEPTED.value, Ack.ACCEPTED_BY_945.value):
            p.checks.append(_check("order state", "no 997 has accepted the 940 yet", found=sent.ack_status,
                                   expected="Accepted"))
        st = sh.parties.get("ST")
        want = locked.get("ship_to") or {}
        if st is not None and want:
            def norm(v: Any) -> str:
                return "".join(ch for ch in str(v or "").upper() if ch.isalnum())
            found_zip, want_zip = norm(st.zip), norm(want.get("zip"))
            if found_zip and want_zip and found_zip != want_zip:
                p.checks.append(_check("mismatch with the 940", "the ship-to differs from the 940",
                                       found=f"{st.name} {st.zip}", expected=f"{want.get('name')} {want.get('zip')}"))
        today = self.clock().astimezone(TORONTO).date()
        if ship is not None and ship > today:
            p.checks.append(_check("ship date", f"the ship date {ship} is in the future", found=ship, expected=f"≤ {today}"))
        if ship is not None and sent is not None and sent.sent_at is not None and ship < sent.sent_at.astimezone(TORONTO).date():
            p.checks.append(_check("ship date", f"the ship date {ship} is before the 940 was sent",
                                   found=ship, expected=f"≥ {sent.sent_at.astimezone(TORONTO).date()}"))

    # ── applying ─────────────────────────────────────────────────────────────
    async def apply(self, uow, doc: Document, set_index: int, sh: Shipment945, report: ShipmentReport,
                    *, run_id: int | None = None) -> Shipment | None:
        s = uow.session
        if (await s.execute(select(Shipment.id).where(Shipment.document_id == doc.id,
                                                      Shipment.set_index == set_index))).scalar_one_or_none() is not None:
            return None  # applied before: nothing changes (AC-OB-36)
        p = await self.plan(s, doc, sh)
        order = await s.get(Order, p.order_id, with_for_update=True) if p.order_id else None
        if order is not None and order.source_type == FROM_945:
            # Made from a shadow-run 945: Zoho's, also after the switch-off. A later part only joins it.
            shipment = await self._store(s, doc, set_index, sh, p, applied=False, shadow=True)
            await attach(s, order, shipment)
            await oplog.write(s, "INFO", STEP, f"945 {doc.remote_name}: joined order {order.label}, made from a 945; not applied",
                              run_id=run_id, document_id=doc.id, order_id=order.id)
            return shipment
        shipment = await self._store(s, doc, set_index, sh, p, applied=order is not None and not p.second_of, shadow=False)
        report.shipments += 1
        for c in p.checks:
            await oplog.write(s, "WARNING", "945 validation", f"945 {doc.remote_name}: {c['check']}: {c['message']}",
                              run_id=run_id, document_id=doc.id, order_id=p.order_id, context=c)
        if p.checks:  # FR-OB-25d: only notifies — one notification per shipment, every check in it
            await self.tasks.raise_problem(s, EDI_945_VALIDATION, shipment.id, problem(
                EDI_945_VALIDATION, f"945 checks: {len(p.checks)} on shipment {shipment.id} ({doc.remote_name})"[:255],
                f"945 file {doc.remote_name}, shipment {shipment.id}"
                + (f", order {order.label}" if order else "")
                + ". Applied as received; these checks only notify (FR-OB-25d):\n"
                + "\n".join(f"- {c['check']}: {c['message']}" for c in p.checks)))
        if order is None:
            report.no_order += 1
            await self.tasks.raise_problem(s, EDI_945_NO_ORDER, shipment.id, problem(
                EDI_945_NO_ORDER, f"945 matches no order: {sh.order_key_raw}"[:255],
                f"945 file {doc.remote_name}: order key {sh.order_key_raw!r} — {p.match_note}. The shipment is stored "
                f"with its {len(sh.rows)} row(s) (shipment {shipment.id}). Create the order it belongs to (a reship or a "
                "customer-service send), or tell the warehouse."))
            return shipment
        order.warehouse_order_number_received = (sh.order_key_raw or "")[:40]
        if p.second_of:
            report.second += 1
            if p.status_to:
                FULFILLMENT.transition(order, F.ON_HOLD, actor=uow.actor, events=uow.events,
                                       reason="a second, different 945 (FR-OB-25)")
            await self.tasks.raise_problem(s, SECOND_945, order.id, problem(
                SECOND_945, f"Second 945 for order {order.label}",
                f"Order {order.label} already shipped by "
                f"{', '.join(p.second_of)}; {doc.remote_name} differs and was stored without changing any quantity. "
                "The order is On Hold: compare both 945s and decide.", urgent=True))
            return shipment
        for lid, info in p.lines.items():
            line = await s.get(OrderLine, int(lid))
            line.shipped_ea = info["shipped_ea"]
        if p.accept_940:
            sent = await latest_940(s, order.id, for_update=True)
            await self.outbound.accept_by_945(uow, sent, f"945 {doc.remote_name} arrived before any 997")
        if p.status_to == F.SHIPPED.value:
            FULFILLMENT.transition(order, F.SHIPPED, actor=uow.actor, events=uow.events,
                                   reason=f"945 {doc.remote_name}" + (" (short)" if p.short else ""))
        if p.short:
            await self.tasks.raise_problem(s, SHORT_SHIPMENT, order.id, problem(
                SHORT_SHIPMENT, f"Short shipment: order {order.label}",
                f"Order {order.label}: " + "; ".join(p.short)
                + f". 945 {doc.remote_name} closed the fulfilment; no second 940 is sent. Decide whether to send the rest."))
        if p.unmatched:
            await self.tasks.raise_problem(s, EDI_945_LINE_UNMATCHED, shipment.id, problem(
                EDI_945_LINE_UNMATCHED, f"945 line matches no order line: order {order.label}",
                f"945 {doc.remote_name} for order {order.label}: " + "; ".join(p.unmatched)
                + ". It was stored but cannot be invoiced until someone decides."))
        if order.source_type == "SHOPIFY":
            shipment.shopify_status = "Pending"
            OUTBOX.add(s, FULFILL, {"shipment_id": shipment.id}, entity=shipment)
        await s.flush()
        return shipment

    async def _store(self, s, doc: Document, set_index: int, sh: Shipment945, p: Plan, *, applied: bool,
                     shadow: bool) -> Shipment:
        """The shipment and every row as received. A shadow shipment is never
        applied (no quantity, no status, no Shopify write — ever)."""
        shipment = Shipment(
            document_id=doc.id, set_index=set_index, set_control=sh.control[:9] or None, order_id=p.order_id,
            order_key_received=(sh.order_key_raw or "")[:40] or None, ship_date=sh.ship_date,
            ship_date_source=sh.ship_date_source, carrier=sh.carrier, mode=sh.mode, tracking=sh.tracking or None,
            bol=sh.bol, carrier_ref=sh.carrier_ref, our_reference=(sh.reference or "")[:80] or None,
            ship_to=asdict(sh.parties["ST"]) if "ST" in sh.parties else None, applied=applied, shadow=shadow,
            checks=p.checks or None, shopify_status="Not needed", version=1)
        s.add(shipment)
        await s.flush()
        by_pos = {(r.lx_index, r.w12_index): r for r in p.rows}
        for r in sh.rows:
            rp = by_pos.get((r.lx_index, r.w12_index))
            s.add(ShipmentRow(
                shipment_id=shipment.id, lx_index=r.lx_index, w12_index=r.w12_index, lx_number=r.lx_number, sscc=r.sscc,
                item_code=r.item_code, upc=r.upc, lot=r.lot, expiry=r.expiry, qty_ordered_ea=r.qty_ordered,
                qty_shipped_ea=r.qty_shipped, weight=r.weight, tracking=r.tracking,
                # A shadow-run 945's rows are linked to the lines too (user 2026-11-03): its quantities show,
                # marked as the shadow run's, without applying anything (applied stays false).
                order_line_id=rp.order_line_id if rp else None,
                needs_review=bool(applied and rp is not None and rp.order_line_id is None)))
        if p.order_id is not None and (await s.execute(select(DocumentLink.id).where(
                DocumentLink.document_id == doc.id, DocumentLink.entity_type == "order",
                DocumentLink.entity_id == p.order_id))).first() is None:
            s.add(DocumentLink(document_id=doc.id, entity_type="order", entity_id=p.order_id))
        await s.flush()
        return shipment

    async def _shadow_shipped(self, s, order_id: int, doc_id: int) -> dict[int, int]:
        """EA per order line that the order's other shadow-run 945s (earlier parts) shipped."""
        rows = (await s.execute(select(ShipmentRow.order_line_id, func.sum(ShipmentRow.qty_shipped_ea))
                                .join(Shipment, Shipment.id == ShipmentRow.shipment_id)
                                .where(Shipment.order_id == order_id, Shipment.shadow.is_(True), Shipment.document_id != doc_id,
                                       ShipmentRow.order_line_id.is_not(None))
                                .group_by(ShipmentRow.order_line_id))).all()
        return {lid: int(n) for lid, n in rows}

    async def link_shadow_rows(self, uow, *, dry_run: bool) -> dict[str, int]:
        """Once, for the shadow-run 945s stored before their rows were linked: each
        shipment with an order and unlinked rows is planned again from its stored
        945 (the same matching, earlier parts first) and its rows get their order
        line. Nothing else is written: no status, no quantity, no task, no Shopify
        write, no check. Idempotent: a shipment whose rows are linked is skipped.
        A synthetic test 945 (its file named SYNTHETIC-…) is left alone. With
        `dry_run` the links are made and rolled back, and the counts reported."""
        s = uow.session
        counts = {"shipments": 0, "skipped_linked": 0, "skipped_synthetic": 0, "no_order": 0, "rows_linked": 0, "rows_unmatched": 0}
        ships = list((await s.execute(select(Shipment).where(Shipment.shadow.is_(True))
                                      .order_by(Shipment.document_id, Shipment.set_index))).scalars())
        for shp in ships:
            doc = await s.get(Document, shp.document_id)
            if (doc.remote_name or "").startswith(SYNTHETIC_PREFIX):  # a made-up test 945: no figures on a real order
                counts["skipped_synthetic"] += 1
                continue
            if shp.order_id is None:
                counts["no_order"] += 1
                continue
            rows = list((await s.execute(select(ShipmentRow).where(ShipmentRow.shipment_id == shp.id))).scalars())
            if all(r.order_line_id is not None for r in rows):
                counts["skipped_linked"] += 1
                continue
            content = (await s.execute(select(Document.content).where(Document.id == doc.id))).scalar_one()
            sh = parse_945(content)[shp.set_index - 1]
            p = await self.plan(s, doc, sh)
            by_pos = {(r.lx_index, r.w12_index): r.order_line_id for r in p.rows}
            counts["shipments"] += 1
            for r in rows:
                lid = by_pos.get((r.lx_index, r.w12_index))
                if lid is None:
                    counts["rows_unmatched"] += 1
                elif r.order_line_id is None:
                    r.order_line_id = lid
                    counts["rows_linked"] += 1
            await s.flush()  # the next part of the same order counts these
        if dry_run:
            await s.rollback()
        else:
            await oplog.write(s, "INFO", STEP, "shadow-run 945 rows linked to their order lines: " + ", ".join(
                f"{k} {v}" for k, v in counts.items()), context=counts)
        return counts

    # ── Shopify write-back (outbox) ──────────────────────────────────────────
    def fulfill_kind(self) -> OutboxKind:
        return OutboxKind(effect=self._fulfill, policy=ONCE, pre_check=self._pre_check, on_done=self._fulfilled,
                          on_failed=self._write_failed, on_suppressed=self._suppressed)

    def close_kind(self) -> OutboxKind:
        return OutboxKind(effect=self._close, policy=ONCE, pre_check=self._pre_check, on_done=self._closed,
                          on_failed=self._write_failed, on_suppressed=self._suppressed)

    async def _pre_check(self, payload: dict[str, Any], uow) -> str | None:
        """The second barrier: nothing is written during the shadow run or for
        a shadow shipment (also after the switch-off)."""
        s = uow.session
        sh = await s.get(Shipment, payload["shipment_id"], with_for_update=True)
        if sh is None or sh.order_id is None:
            return "the shipment or its order is gone"
        if await SETTINGS.get(s, SHADOW_MODE) or sh.shadow:
            await oplog.write(s, "WARNING", STEP, f"shadow run: no Shopify write for shipment {sh.id}", order_id=sh.order_id)
            sh.shopify_status = "Suppressed"
            return "the shadow run: nothing is written to Shopify"
        if not sh.applied:
            return "the shipment was not applied"
        return None

    async def _context(self, shipment_id: int):
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            s = uow.session
            sh = await s.get(Shipment, shipment_id)
            order = await s.get(Order, sh.order_id)
            conn = await s.get(Connection, order.connection_id) if order.connection_id else None
            if conn is None or conn.kind != "SHOPIFY":
                raise BusinessError("shopify.no_connection", f"order {order.label} has no Shopify connection")
            lines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars())
            return sh, order, ConnectionSnapshot.of(conn), lines

    @staticmethod
    def _tracking(sh: Shipment) -> dict[str, Any] | None:
        number = (sh.tracking or [None])[0]
        return {"number": number, "company": TRACKING_COMPANY} if number else None

    async def _fulfill(self, payload: dict[str, Any], ctx) -> None:
        sh, order, conn, lines = await self._context(payload["shipment_id"])
        oid = order.source_order_id
        tracking = self._tracking(sh)
        existing = await self.shopify.fulfillments(conn, oid)  # always first: a retry never duplicates
        number = (tracking or {}).get("number")
        mine = next((f for f in existing if number and f.get("tracking_number") == number), None)
        if mine is not None:
            await self._record(sh.id, str(mine["id"]), "already in Shopify")
            return
        zoho = next((f for f in existing if f.get("status") in ("success", "open", "pending") and not f.get("tracking_number")
                     and (order.shopify_fulfillment_id is None or str(f["id"]) == str(order.shopify_fulfillment_id))), None)
        if zoho is not None:  # AC-OB-72: Zoho fulfilled it at the 997, without tracking
            if tracking:
                f = await self.shopify.update_tracking(conn, str(zoho["id"]), tracking, True)
                await self._record(sh.id, str(f["id"]), "tracking added to the existing fulfillment")
            else:
                await self._record(sh.id, str(zoho["id"]), "already fulfilled; no tracking to add")
            return
        shipped_units = {}
        for ln in lines:  # EA back to Shopify units (the line's factor); a part unit is not fulfilled
            if ln.shipped_ea and ln.factor_ea:
                shipped_units[str(ln.line_no)] = int(ln.shipped_ea // int(ln.factor_ea))
        items = []
        for fo in await self.shopify.fulfillment_orders(conn, oid):
            if fo.get("status") not in ("open", "in_progress"):
                continue
            fo_lines = []
            for li in fo.get("line_items", []):
                want = shipped_units.get(str(li.get("line_item_id")), 0)
                qty = min(want, int(li.get("fulfillable_quantity") or 0))
                if qty > 0:
                    fo_lines.append({"id": li["id"], "quantity": qty})
                    shipped_units[str(li.get("line_item_id"))] = want - qty
            if fo_lines:
                items.append({"fulfillment_order_id": fo["id"], "fulfillment_order_line_items": fo_lines})
        if not items:
            raise RuntimeError("no open fulfillment order line to fulfil in Shopify")
        f = await self.shopify.create_fulfillment(conn, items, tracking, True)
        await self._record(sh.id, str(f["id"]), "fulfillment created")

    async def _record(self, shipment_id: int, fulfillment_id: str, how: str) -> None:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            sh = await uow.session.get(Shipment, shipment_id, with_for_update=True)
            sh.shopify_fulfillment_id = fulfillment_id
            order = await uow.session.get(Order, sh.order_id, with_for_update=True)
            order.shopify_fulfillment_id = fulfillment_id
            await oplog.write(uow.session, "INFO", STEP, f"Shopify: {how} ({fulfillment_id})", order_id=order.id)
            await uow.commit()

    async def _fulfilled(self, row, exc, uow) -> None:
        sh = await uow.session.get(Shipment, row.payload["shipment_id"], with_for_update=True)
        sh.shopify_status, sh.shopify_error = "Pending", None
        OUTBOX.add(uow.session, CLOSE, {"shipment_id": sh.id}, entity=sh)  # FR-OB-11a: then archive
        await self.tasks.resolve_problem(uow.session, SHOPIFY_WRITE_FAILED, sh.id)

    async def _close(self, payload: dict[str, Any], ctx) -> None:
        sh, order, conn, _ = await self._context(payload["shipment_id"])
        await self.shopify.close_order(conn, order.source_order_id)

    async def _closed(self, row, exc, uow) -> None:
        sh = await uow.session.get(Shipment, row.payload["shipment_id"], with_for_update=True)
        sh.shopify_status, sh.shopify_error = "Done", None
        await self.tasks.resolve_problem(uow.session, SHOPIFY_WRITE_FAILED, sh.id)

    async def _write_failed(self, row, exc, uow) -> None:
        s = uow.session
        sh = await s.get(Shipment, row.payload["shipment_id"], with_for_update=True)
        order = await s.get(Order, sh.order_id)
        what = "archiving the order" if row.kind == CLOSE else "the fulfillment"
        why = shopify_readable(exc) if exc is not None else "unknown"
        hint = (" The app lacks a Shopify permission (scope) — e.g. write_orders for archiving; grant it, then Retry."
                if isinstance(exc, ShopifyForbidden) else " Check the order in Shopify, then Retry.")
        sh.shopify_status, sh.shopify_error = "Failed", f"{what}: {why}"[:2000]
        await self.tasks.raise_problem(s, SHOPIFY_WRITE_FAILED, sh.id, problem(
            SHOPIFY_WRITE_FAILED, f"Shopify {what} failed: order {order.label}"[:255],
            f"Order {order.label}: {what} failed: {why}.{hint} "
            "It is not retried automatically."))

    async def _suppressed(self, row, exc, uow) -> None:
        sh = await uow.session.get(Shipment, row.payload["shipment_id"], with_for_update=True)
        sh.shopify_status, sh.shopify_error = "Suppressed", str(exc)[:2000] if exc else None
        await oplog.write(uow.session, "INFO", STEP, f"Shopify write for shipment {sh.id} refused by the guard: {exc}",
                          order_id=sh.order_id)
        await send_refused(self.tasks, uow.session, row, f"the Shopify write for shipment {sh.id}", exc)

    async def retry_shopify(self, uow, shipment_id: int) -> Shipment:
        """A person's retry (sales.order.operate): one new row; the effect reads
        the order's fulfillments first, so nothing is created twice."""
        s = uow.session
        sh = await s.get(Shipment, shipment_id, with_for_update=True)
        if sh is None:
            raise BusinessError("shipment.not_found", "no such shipment", status_code=404)
        if sh.shopify_status not in ("Failed", "Suppressed"):
            raise BusinessError("shipment.not_failed", f"the Shopify write is {sh.shopify_status}")
        if sh.shadow or await SETTINGS.get(s, SHADOW_MODE):
            raise BusinessError("shipment.shadow", "the shadow run: nothing is written to Shopify")
        kind = CLOSE if sh.shopify_fulfillment_id else FULFILL
        sh.shopify_status = "Pending"
        OUTBOX.add(s, kind, {"shipment_id": sh.id}, entity=sh)
        await s.flush()
        return sh

