"""An order from a 945 that has none (FR-OB-25c, AC-OB-38).

A 945 matching no order is kept with its rows. A person (or the one-off script)
makes the order from it: partner and PO from the reference Zoho wrote in the
945 (`CRM-940_<warehouse order no>_<partner name>_<PO>`), else the partner they
choose; one line per item in EA; the ship-to, ship date and warehouse order
number as received. The shipment and its rows are then linked to it.

Only for a shadow-run 945 while the shadow run is on. What such an order does
after a 945 applied outside the shadow run is not decided, so that is refused.

The goods already left and Zoho handles the rest, so nothing here may start
anything: `shadow` is true (kept after the switch-off), the source is FROM_945
(its own barrier in `qualifies` / `eligible_clause`), the invoice status is Not
Required, no task is raised, `shipped_ea` is not written (Shipped stays derived,
from the shadow 945's rows). The status is Pending: what a shadow order with a shadow
945 already has; Shipped would claim the 945 was applied.

A later part of the same warehouse order joins this order: `find_orders` finds it
by the warehouse number, and `attach` adds what the part shipped to its lines.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select

from app.domain.edi945 import OrderKey, order_key, zoho_reference
from app.domain.orders import FROM_945
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.models.config import Item, Partner
from app.models.documents import Document, DocumentLink
from app.models.orders import Order, OrderLine
from app.models.shadow import ShadowMatch
from app.models.shipments import Shipment, ShipmentRow
from app.platform.authorization import requires
from app.platform.errors import BusinessError, InvalidInput
from app.services import oplog
from app.services.orders import Catalog, order_ref
from app.services.settings import SETTINGS, SHADOW_MODE

READ = "sales.read"
OPERATE = "sales.order.operate"
STEP = "945"
SYNTHETIC_PREFIX = "SYNTHETIC-"  # the made-up 945s fed to the dev servers by hand
NOT_IN_MASTER = "item not in the Item Master"


def warehouse_number_is(key: OrderKey):
    """The clause finding an order made from an earlier part of this warehouse order."""
    number = Order.warehouse_order_number_received
    return func.nullif(func.ltrim(number, "0"), "") == key.base if key.numeric else number == key.base


def _warehouse_number(key: OrderKey) -> str | None:
    """As received, without a part's letter: "001008597a" → "001008597"."""
    raw = key.received.strip()
    return (raw[:-1] if key.suffix else raw)[:40] or None


def _ship_to(party: dict[str, Any] | None) -> dict[str, Any] | None:
    """The 945's N1*ST in the order's address shape."""
    if not party:
        return None
    number = str(party.get("id") or "")
    return {k: party.get(k) for k in ("name", "address1", "city", "province", "zip", "country")} | (
        {"gln": number} if number.isdigit() else {})


def _row_key(r: ShipmentRow) -> str:
    return r.item_code or r.upc or f"LX {r.lx_index} W12 {r.w12_index}"


def _item(catalog: Catalog, r: ShipmentRow) -> Item | None:
    return catalog.by_code.get((r.item_code or "").casefold()) or catalog.by_upc.get((r.upc or "").casefold())


def _by_item(rows: list[ShipmentRow]) -> dict[str, list[ShipmentRow]]:
    out: dict[str, list[ShipmentRow]] = {}
    for r in rows:
        out.setdefault(_row_key(r), []).append(r)
    return out


def ordered_ea(rows: list[ShipmentRow], shipped: int) -> int:
    """W12-02 repeats the item's whole ordered quantity on each pallet's row, so it
    is never summed: the largest one, or what shipped if that is more."""
    return max(max((r.qty_ordered_ea or 0 for r in rows), default=0), shipped)


@dataclass
class PlanLine:
    item_code: str | None     # None: the row names no item the Item Master has
    received: str             # what the 945 calls it
    qty_ea: int
    shipped_ea: int
    problem: str | None


@dataclass
class Plan:
    """What making the order would do — computed, nothing written."""
    shipment_id: int
    document_id: int
    order_key: str | None
    reference: str | None
    partner_code: str | None      # from the reference; None: a person chooses
    partner_name_received: str | None
    po: str | None
    ship_date: date | None
    ship_to: dict[str, Any] | None
    lines: list[PlanLine] = field(default_factory=list)
    order_id: int | None = None   # a later part: the order it joins
    order_ref: str | None = None
    refused: str | None = None    # the error code that stops it
    refused_message: str | None = None


class OrderFrom945:
    def __init__(self, uow) -> None:
        self.uow = uow
        self.s = uow.session

    # ── reading ──────────────────────────────────────────────────────────────
    @requires(READ, system=True)
    async def without_order(self, document_id: int | None = None) -> list[Plan]:
        """The 945s with no order, each with what making its order would do."""
        q = select(Shipment).where(Shipment.order_id.is_(None)).order_by(Shipment.id)
        if document_id is not None:
            q = q.where(Shipment.document_id == document_id)
        return [await self._plan(sh, None) for sh in (await self.s.execute(q)).scalars()]

    async def _rows(self, shipment_id: int) -> list[ShipmentRow]:
        return list((await self.s.execute(select(ShipmentRow).where(ShipmentRow.shipment_id == shipment_id)
                                          .order_by(ShipmentRow.lx_index, ShipmentRow.w12_index))).scalars())

    async def _partner_named(self, name: str) -> Partner | None:
        found = list((await self.s.execute(select(Partner).where(
            func.lower(Partner.name) == name.strip().lower(), Partner.active.is_(True)))).scalars())
        return found[0] if len(found) == 1 else None

    async def _existing_part(self, key: OrderKey) -> Order | None:
        if key.base is None:
            return None
        return (await self.s.execute(select(Order).where(Order.source_type == FROM_945, warehouse_number_is(key))
                                     .order_by(Order.id).limit(1))).scalars().first()

    async def _plan(self, sh: Shipment, chosen: str | None) -> Plan:
        ref = zoho_reference(sh.our_reference)
        key = order_key(sh.order_key_received or "")
        named = await self._partner_named(ref.partner_name) if ref else None
        p = Plan(shipment_id=sh.id, document_id=sh.document_id, order_key=sh.order_key_received, reference=sh.our_reference,
                 partner_code=named.code if named else None, partner_name_received=ref.partner_name if ref else None,
                 po=ref.po if ref else None, ship_date=sh.ship_date, ship_to=_ship_to(sh.ship_to))
        catalog = await Catalog.load(self.s)
        for received, rows in _by_item(await self._rows(sh.id)).items():
            item = _item(catalog, rows[0])
            shipped = sum(r.qty_shipped_ea for r in rows)
            p.lines.append(PlanLine(item.item_code if item else None, received, ordered_ea(rows, shipped), shipped,
                                    None if item else NOT_IN_MASTER))
        code, message = await self._refusal(sh)
        if code:
            p.refused, p.refused_message = code, message
            return p
        order = await self._existing_part(key)
        if order is None:
            order, code, message = await self._by_source(p, chosen or p.partner_code, key)
            p.refused, p.refused_message = code, message
        if order is not None:
            p.order_id, p.order_ref = order.id, order.ref
        return p

    async def _refusal(self, sh: Shipment) -> tuple[str | None, str | None]:
        if sh.order_id is not None:
            return "order945.has_order", "this 945 already has an order"
        if not sh.shadow or not await SETTINGS.get(self.s, SHADOW_MODE):
            return ("order945.outside_shadow", "an order is made from a 945 only during the shadow run: what it should do "
                    "outside it (a 940, an invoice, a Shopify write) is not decided yet")
        name = (await self.s.execute(select(Document.remote_name).where(Document.id == sh.document_id))).scalar_one()
        if (name or "").startswith(SYNTHETIC_PREFIX):
            return "order945.synthetic", "this is a made-up test 945: it makes no order"
        return None, None

    def _source_id(self, p: Plan, key: OrderKey) -> str:
        return (p.po or f"945-{key.base or f'shipment-{p.shipment_id}'}")[:64]

    def _number(self, p: Plan, key: OrderKey) -> str:
        """What follows the abbreviation in the Order ref."""
        return p.po or _warehouse_number(key) or f"945-{p.shipment_id}"

    async def _by_source(self, p: Plan, partner_code: str | None, key: OrderKey) -> tuple[Order | None, str | None, str | None]:
        """The same PO for this partner: another part of a FROM_945 order, or an
        order of another source — then the 945 belongs to it, and a second order is refused."""
        if partner_code is None:
            return None, None, None
        partner = (await self.s.execute(select(Partner).where(Partner.code == partner_code))).scalar_one_or_none()
        same = (await self.s.execute(select(Order).where(or_(
            (Order.partner_code == partner_code) & (Order.source_order_id == self._source_id(p, key)),
            Order.source_reference == order_ref(partner, self._number(p, key)))).order_by(Order.id).limit(1))).scalars().first()
        if same is None or same.source_type == FROM_945:
            return same, None, None
        return None, "order945.exists", f"order {same.label} already has this PO: this 945 belongs to it, not to a new order"

    # ── writing ──────────────────────────────────────────────────────────────
    @requires(OPERATE, system=True)
    async def create(self, shipment_id: int, partner_code: str | None = None) -> Order:
        """Make the order (or join the part's order), link the shipment and its rows."""
        sh = await self.s.get(Shipment, shipment_id, with_for_update=True)
        if sh is None:
            raise BusinessError("shipment.not_found", "no such shipment", status_code=404)
        p = await self._plan(sh, partner_code)
        if p.refused:
            raise BusinessError(p.refused, p.refused_message)
        self.uow.set_reason(f"order from 945: shipment {sh.id}")
        order = await self.s.get(Order, p.order_id, with_for_update=True) if p.order_id else await self._new(
            sh, p, await self._partner(partner_code or p.partner_code))
        await self._link(sh, order)
        await oplog.write(self.s, "INFO", STEP, f"order {order.label} from 945 shipment {sh.id}: {len(p.lines)} line(s), "
                          "shadow run, nothing applied", document_id=sh.document_id, order_id=order.id)
        return order

    async def _partner(self, code: str | None) -> Partner:
        if not code:
            raise InvalidInput("order945.partner_required", "this 945 names no partner Sales knows: choose one",
                               {"field": "partner_code"})
        p = (await self.s.execute(select(Partner).where(Partner.code == code))).scalar_one_or_none()
        if p is None or not p.active:
            raise InvalidInput("partner.unknown", f"no active partner {code!r}", {"field": "partner_code"})
        if not (p.abbrev or "").strip():
            raise InvalidInput("partner.no_abbrev", f"partner {p.code} has no abbreviation: set it under "
                               "Configuration → Partners (it starts the Order ref)", {"field": "partner_code"})
        return p

    async def _new(self, sh: Shipment, p: Plan, partner: Partner) -> Order:
        key = order_key(sh.order_key_received or "")
        order = Order(
            partner_code=partner.code, source_type=FROM_945, source_order_id=self._source_id(p, key),
            source_order_number=p.po, source_reference=order_ref(partner, self._number(p, key)),
            order_date=sh.ship_date, ship_to=p.ship_to, warehouse_order_number_received=_warehouse_number(key),
            notes=f"Created from 945 document {sh.document_id}. No prices: the 945 carries none.",
            fulfillment_status=F.PENDING.value, invoice_status=Inv.NOT_REQUIRED.value, shadow=True,
            first_received_at=datetime.now(timezone.utc), version=1)
        self.s.add(order)
        await self.s.flush()
        return order

    async def _link(self, sh: Shipment, order: Order) -> None:
        sh.order_id = order.id
        if (await self.s.execute(select(DocumentLink.id).where(
                DocumentLink.document_id == sh.document_id, DocumentLink.entity_type == "order",
                DocumentLink.entity_id == order.id))).first() is None:
            self.s.add(DocumentLink(document_id=sh.document_id, entity_type="order", entity_id=order.id))
        await attach(self.s, order, sh)
        match = (await self.s.execute(select(ShadowMatch).where(ShadowMatch.document_id == sh.document_id)
                                      .with_for_update())).scalar_one_or_none()
        if match is not None:  # Shadow compare stops saying this 945 has no Sales order
            match.order_id, match.partner_code = order.id, order.partner_code
            match.status = "Match" if match.status == "No Sales order" else match.status
            match.note = f"order {order.label} created from this 945"
        await self.s.flush()


async def attach(s, order: Order, sh: Shipment) -> None:
    """What this shipment shipped onto the order's lines: a line per item, its
    quantity covering every part so far, each row linked. No price, no `shipped_ea`."""
    lines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == order.id)
                                  .order_by(OrderLine.line_no).with_for_update())).scalars())
    by_key: dict[str, OrderLine] = {}
    for ln in lines:
        for k in (ln.item_code, ln.source.get("item_code"), ln.source.get("upc"), ln.source.get("row")):
            if k:
                by_key.setdefault(k, ln)
    next_no = max((ln.line_no for ln in lines), default=0) + 1
    catalog = await Catalog.load(s)
    rows = list((await s.execute(select(ShipmentRow).where(ShipmentRow.shipment_id == sh.id)
                                 .order_by(ShipmentRow.lx_index, ShipmentRow.w12_index))).scalars())
    for key, group in _by_item(rows).items():
        ln = by_key.get(key)
        if ln is None:
            first = group[0]
            item = _item(catalog, first)
            ids = {"item_code": first.item_code, "upc": first.upc, "row": None if first.item_code or first.upc else key}
            ln = OrderLine(order_id=order.id, line_no=next_no, item_code=item.item_code if item else None,
                           source={k: v for k, v in ids.items() if v}, ordered_qty=0, order_unit="EA", factor_ea=1, qty_ea=0,
                           problem=None if item else NOT_IN_MASTER)
            next_no += 1
            s.add(ln)
            await s.flush()
            by_key[key] = ln
        before = (await s.execute(select(func.coalesce(func.sum(ShipmentRow.qty_shipped_ea), 0)).where(
            ShipmentRow.order_line_id == ln.id, ShipmentRow.shipment_id != sh.id))).scalar_one()
        shipped = int(before) + sum(r.qty_shipped_ea for r in group)
        ln.qty_ea = max(ln.qty_ea or 0, ordered_ea(group, shipped))
        ln.ordered_qty = ln.qty_ea
        for r in group:
            r.order_line_id = r.order_line_id or ln.id
    await s.flush()
