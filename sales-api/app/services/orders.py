"""Orders from their sources (P3): one upsert for Shopify orders and retailer
850 POs, review resolution and manual orders.

Upsert (FR-OB-08, -16; AC-OB-16): the same (partner, source order id) updates
the order it already has. Lines are matched to the Item Master and converted
to EA (FR-OB-02, -14); anything that cannot be — unknown partner or item, no
conversion, a fraction of an EA, zero quantity, a case pack that differs from
the Item Master, a Walmart order without DC routing — puts the order in Review
with one task listing what was received (FR-OB-15, AC-OB-13/15). Statuses
change only through the machines (§4.5). No 940 exists before P4, so every
update here is "before the 940": the order and its lines are updated; the
after-940 branch (locked values, before/after task) arrives with sales_940s.

Payment (FR-OB-12, D20): a PREPAID order not yet paid waits in Payment Hold;
`paid_at` records when payment became satisfied — the start of the 940 delay —
and is never moved by later updates. Archive (FR-OB-03d): archived in Shopify
before any 940 → Archive Suppressed, recorded on the order, and one task when
payment was satisfied (not for refunded / voided or historical orders);
un-archived → Pending again and the task completes. Source cancellation
before the 940 cancels silently (FR-OB-18).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.domain.address import address_problem
from app.domain.items import barcode_key, titles_list
from app.domain.order_rules import payment_satisfied, to_ea, unit_from_title
from app.domain.sources import SourceLine, SourceOrder
from app.domain.status import FULFILLMENT, Fulfillment as F
from app.domain.status import Invoice as Inv
from app.integrations.shopify import readable
from app.models.config import DcRouting, Item, ItemPartnerCode, Partner
from app.models.documents import DocumentLink
from app.models.orders import Order, OrderLine
from app.services.outbound import changed_after_940
from app.services.problems import CANCELLED_AFTER_940, ORDER_ARCHIVED, ORDER_REVIEW, problem
from app.services.settings import SETTINGS, SHADOW_MODE

UPDATABLE = {F.REVIEW, F.PAYMENT_HOLD, F.PENDING, F.ARCHIVE_SUPPRESSED, F.DRAFT}
# After a 940 the order is still updated, but what the 940 carried stays locked
# on the 940 record and a difference raises a task (FR-OB-16). Shipped / On
# Hold lines carry 945 data and are never rebuilt.
AFTER_940 = {F.GENERATED_940, F.SENT_940, F.ACCEPTED_940, F.REJECTED_940, F.TIMEOUT_940, F.FAILED_940}
_HEADER_COLUMNS = (
    "order_type", "department", "order_date", "requested_delivery_date", "do_not_deliver_after", "cancel_date",
    "ship_to", "bill_to", "contact", "notes", "currency", "subtotal", "tax", "shipping", "discount", "total",
    "shopify_financial_status", "shopify_fulfillment_status", "shipping_method",
)


def order_ref(partner: Partner | None, number: str | None) -> str | None:
    """The Order ref (A.3, user 2026-11-03): the partner's abbreviation, "-", the source
    order number. None without an abbreviation (the order goes to review). Set at
    creation only — a later abbreviation change leaves existing refs as they are."""
    abbrev = (partner.abbrev or "").strip() if partner is not None else ""
    return f"{abbrev}-{number}" if abbrev and number else None


def now() -> datetime:
    return datetime.now(timezone.utc)


# ── the Item Master as one lookup ────────────────────────────────────────────
@dataclass
class Catalog:
    by_sku: dict[str, Item] = field(default_factory=dict)
    by_code: dict[str, Item] = field(default_factory=dict)
    by_gtin: dict[str, Item] = field(default_factory=dict)
    by_upc: dict[str, Item] = field(default_factory=dict)
    by_gtin_key: dict[str, list[Item]] = field(default_factory=dict)    # barcode_key → items (Shopify)
    by_upc_key: dict[str, list[Item]] = field(default_factory=dict)
    buyer_parts: dict[tuple[str, str], str] = field(default_factory=dict)  # (partner, part) → item code
    dc_glns: set[tuple[str, str]] = field(default_factory=set)              # (partner, gln), active

    @classmethod
    async def load(cls, session) -> Catalog:
        c = cls()
        for it in (await session.execute(select(Item).where(Item.active.is_(True)))).scalars():
            c.by_code[it.item_code.casefold()] = it
            for attr, index in (("sku", c.by_sku), ("gtin", c.by_gtin), ("upc", c.by_upc)):
                if getattr(it, attr):
                    index[getattr(it, attr).strip().casefold()] = it
            for attr, keyed in (("gtin", c.by_gtin_key), ("upc", c.by_upc_key)):
                if (k := barcode_key(getattr(it, attr))) is not None:
                    keyed.setdefault(k, []).append(it)
        for pc in (await session.execute(select(ItemPartnerCode))).scalars():
            c.buyer_parts[(pc.partner_code.casefold(), pc.buyer_part.strip().casefold())] = pc.item_code
        for d in (await session.execute(select(DcRouting).where(DcRouting.active.is_(True)))).scalars():
            c.dc_glns.add((d.partner_code.casefold(), d.ship_to_gln.strip()))
        return c

    def units(self, item: Item) -> dict[str, Decimal]:
        return {u.unit: Decimal(u.factor_ea) for u in item.units if u.active}

    def titles(self, item: Item) -> dict[str, list[str]]:
        return {u.unit: titles_list(u.shopify_titles) for u in item.units if u.active}

    def match(self, src: SourceOrder, line: SourceLine) -> tuple[Item | None, str | None]:
        ids = {k: (str(v).strip().casefold() if v not in (None, "") else None) for k, v in line.identifiers.items()}
        if src.source_type == "SHOPIFY":  # A.11 (product owner, 2026-10-30): SKU, then the barcode as GTIN, then UPC
            if ids.get("sku") and (item := self.by_sku.get(ids["sku"])):
                return item, None
            if line.identifiers.get("barcode_error"):
                return None, f"the variant's barcode could not be read from Shopify: {line.identifiers['barcode_error']}"
            key = barcode_key(line.identifiers.get("barcode"))
            for index in (self.by_gtin_key, self.by_upc_key):
                found = index.get(key, []) if key else []
                if len(found) > 1:
                    return None, f"barcode matches several items ({', '.join(sorted(i.item_code for i in found))})"
                if found:
                    return found[0], None
            return None, "item not in the Item Master"
        else:  # AC-OB-12: GTIN, UPC, then this partner's buyer part — never another partner's
            item = ((ids.get("gtin") and self.by_gtin.get(ids["gtin"]))
                    or (ids.get("upc") and self.by_upc.get(ids["upc"])))
            if not item and ids.get("buyer_part"):
                code = self.buyer_parts.get((src.partner_code.casefold(), ids["buyer_part"]))
                item = self.by_code.get(code.casefold()) if code else None
        return (item, None) if item else (None, "item not in the Item Master")


class BarcodeLookup:
    """The barcode of a Shopify line's variant, read through the gateway only
    when the SKU matched no item — one call per variant per run (cached)."""

    def __init__(self, shopify, conn) -> None:
        self.shopify, self.conn = shopify, conn
        self._cache: dict[str, tuple[str | None, str | None]] = {}

    async def get(self, variant_id: str) -> tuple[str | None, str | None]:
        """(barcode, error)."""
        if variant_id not in self._cache:
            try:
                v = await self.shopify.variant(self.conn, variant_id)
                self._cache[variant_id] = ((v.get("barcode") or "").strip() or None, None)
            except Exception as e:  # noqa: BLE001 — the line goes to review with the reason, never a guess
                self._cache[variant_id] = (None, readable(e))
        return self._cache[variant_id]


async def add_barcodes(src: SourceOrder, catalog: Catalog, lookup: BarcodeLookup | None) -> None:
    """Fill `barcode` (or `barcode_error`) on each Shopify line whose SKU
    matches no active item. Read afresh every time, so a barcode fixed in
    Shopify counts at the next re-check."""
    if src.source_type != "SHOPIFY":
        return
    for ln in src.lines:
        ln.identifiers.pop("barcode", None)
        ln.identifiers.pop("barcode_error", None)
        sku = str(ln.identifiers.get("sku") or "").strip().casefold()
        if ln.excluded or (sku and sku in catalog.by_sku):
            continue
        variant_id = str(ln.identifiers.get("variant_id") or "").strip()
        if not variant_id:  # a custom line: no variant, so no barcode to read
            continue
        if lookup is None:
            ln.identifiers["barcode_error"] = "no Shopify connection for this partner to read it from"
        else:
            barcode, error = await lookup.get(variant_id)
            ln.identifiers["barcode" if error is None else "barcode_error"] = barcode if error is None else error


@dataclass
class LinePlan:
    line: SourceLine
    item_code: str | None
    unit: str | None
    factor: Decimal | None
    qty_ea: int | None
    problem: str | None


def plan_lines(src: SourceOrder, catalog: Catalog) -> list[LinePlan]:
    out = []
    for ln in src.lines:
        if ln.excluded:
            out.append(LinePlan(ln, None, ln.unit, None, None, None))
            continue
        item, why = catalog.match(src, ln)
        if item is None:
            out.append(LinePlan(ln, None, ln.unit, None, None, why))
            continue
        units = catalog.units(item)
        from_title = src.source_type == "SHOPIFY" and not ln.unit  # an 850 line without a UOM is never guessed
        unit = unit_from_title(ln.identifiers.get("variant_title"), units, catalog.titles(item)) if from_title else ln.unit
        qty_ea, why = to_ea(ln.quantity, unit, units)
        if unit is None and from_title:
            why = "the variant title looks like a pack size this item has no unit for"
        if why is None and ln.inner_packs is not None and unit == "CASE" and units.get("CASE") is not None \
                and ln.inner_packs != units["CASE"]:  # FR-OB-02 / AC-OB-15: the Item Master decides
            why = f"case pack {ln.inner_packs} on the 850 differs from the Item Master ({units['CASE']})"
        out.append(LinePlan(ln, item.item_code, unit, units.get(unit) if unit else None, qty_ea, why))
    return out


def review_reasons(src: SourceOrder, partner: Partner | None, plans: list[LinePlan], catalog: Catalog) -> list[str]:
    reasons = []
    if not plans:  # no line at all (e.g. a source file read under the wrong key): never a 940 with nothing in it
        reasons.append("the order has no lines (none found in the source file)")
    if partner is None:
        reasons.append(f"unknown partner {src.partner_code}")
    elif not (partner.abbrev or "").strip():
        reasons.append(f"partner {partner.code} has no abbreviation: the order has no Order ref (set it under "
                       "Configuration → Partners)")
    elif partner.dc_routing_required and src.source_type == "EDI_850":  # FR-OB-03: Walmart
        gln = str((src.header.get("ship_to") or {}).get("gln") or "").strip()
        if (partner.code.casefold(), gln) not in catalog.dc_glns:
            reasons.append(f"no DC routing for ship-to {gln or '(none)'}")
    if why := address_problem(src.header.get("ship_to")):  # FR-OB-29a: checked before any 940, offline
        reasons.append(f"ship-to {why}")
    for p in plans:
        if p.problem:
            ids = ", ".join(f"{k}={v}" for k, v in p.line.identifiers.items()
                            if v not in (None, "") and k != "barcode_error")
            reasons.append(f"line {p.line.line_no}: {p.problem} ({ids}; {p.line.quantity} {p.unit or '?'})")
    return reasons


# ── upsert ───────────────────────────────────────────────────────────────────
class OrderSync:
    def __init__(self, uow, tasks) -> None:
        self.uow = uow
        self.s = uow.session
        self.tasks = tasks

    def _to(self, order: Order, status: F, reason: str) -> None:
        FULFILLMENT.transition(order, status, actor=self.uow.actor, events=self.uow.events, reason=reason)

    async def upsert(self, src: SourceOrder, *, connection_id: int | None = None, document_id: int | None = None,
                     received_at: datetime | None = None, barcodes: BarcodeLookup | None = None) -> Order:
        received_at = received_at or now()
        catalog = await Catalog.load(self.s)
        await add_barcodes(src, catalog, barcodes)
        partner = (await self.s.execute(select(Partner).where(Partner.code == src.partner_code))).scalar_one_or_none()
        plans = plan_lines(src, catalog)
        reasons = review_reasons(src, partner, plans, catalog)
        order = (await self.s.execute(select(Order).where(
            Order.partner_code == src.partner_code, Order.source_order_id == src.source_order_id).with_for_update()
        )).scalar_one_or_none()
        if order is None:
            order = await self._create(src, partner, plans, reasons, connection_id, received_at)
        else:
            await self._update(order, src, partner, plans, reasons, received_at)
        if document_id is not None:
            exists = (await self.s.execute(select(DocumentLink.id).where(
                DocumentLink.document_id == document_id, DocumentLink.entity_type == "order",
                DocumentLink.entity_id == order.id))).scalar_one_or_none()
            if exists is None:
                self.s.add(DocumentLink(document_id=document_id, entity_type="order", entity_id=order.id))
        await self.s.flush()
        return order

    def _paid(self, partner: Partner | None, src_type: str, financial: str | None) -> bool:
        return partner is not None and payment_satisfied(partner.settlement_mode, src_type, financial)

    async def _create(self, src, partner, plans, reasons, connection_id, received_at) -> Order:
        h = src.header
        paid = self._paid(partner, src.source_type, h.get("shopify_financial_status"))
        if reasons:
            status = F.REVIEW
        elif partner is not None and partner.settlement_mode == "PREPAID" and not paid:
            status = F.PAYMENT_HOLD
        else:
            status = F.PENDING
        order = Order(
            partner_code=src.partner_code, connection_id=connection_id, source_type=src.source_type,
            source_order_id=src.source_order_id, source_order_number=src.source_order_number,
            source_reference=order_ref(partner, src.source_order_number),
            **{k: h.get(k) for k in _HEADER_COLUMNS if k in h},
            fulfillment_status=status.value,
            invoice_status=(Inv.NOT_REQUIRED if partner is not None and partner.invoice_method == "NONE"
                            else Inv.PENDING).value,
            hold_reason="Payment Required" if status == F.PAYMENT_HOLD else None,
            review_reason="\n".join(reasons) or None,
            historical=bool(h.get("historical")),
            shadow=await SETTINGS.get(self.s, SHADOW_MODE),
            first_received_at=received_at,
            paid_at=received_at if paid and partner is not None and partner.settlement_mode == "PREPAID" else None,
            source_updated_at=src.updated_at,
            version=1,
        )
        self.s.add(order)
        await self.s.flush()
        for p in plans:
            self.s.add(self._line(order, p))
        await self.s.flush()
        if status == F.REVIEW:
            await self._raise_review(order, reasons)
        await self._archive_and_cancel(order, src, partner, paid)
        return order

    def _line(self, order: Order, p: LinePlan) -> OrderLine:
        ln = p.line
        return OrderLine(order_id=order.id, line_no=ln.line_no, item_code=p.item_code,
                         source={k: v for k, v in ln.identifiers.items() if v not in (None, "")} |
                         ({"inner_packs": str(ln.inner_packs)} if ln.inner_packs is not None else {}),
                         ordered_qty=ln.quantity, order_unit=p.unit, factor_ea=p.factor, qty_ea=p.qty_ea,
                         unit_price=ln.unit_price, removed=ln.excluded is not None,
                         line_status="Cancelled" if ln.excluded else "Active",
                         problem=p.problem or ln.excluded)

    async def _update(self, order, src, partner, plans, reasons, received_at) -> None:
        h = src.header
        for k in _HEADER_COLUMNS:
            if k in h:
                setattr(order, k, h[k])
        order.source_updated_at = src.updated_at or order.source_updated_at
        if order.historical is False and h.get("historical"):
            order.historical = True
        if h.get("source_cancelled_at") and order.source_cancelled_at is None and await self._cancelled_after_940(order, h):
            return
        after_940 = order.fulfillment_status in {s.value for s in AFTER_940}
        if order.fulfillment_status not in {s.value for s in UPDATABLE} and not after_940:
            return  # shipped, cancelled, on hold: the lines are never rebuilt (FR-OB-16)
        # Lines before the 940: updated, added, or marked removed when gone.
        existing = {ln.line_no: ln for ln in (await self.s.execute(
            select(OrderLine).where(OrderLine.order_id == order.id))).scalars()}
        seen = set()
        for p in plans:
            seen.add(p.line.line_no)
            fresh = self._line(order, p)
            cur = existing.get(p.line.line_no)
            if cur is None:
                self.s.add(fresh)
                continue
            for k in ("item_code", "source", "ordered_qty", "order_unit", "factor_ea", "qty_ea", "unit_price",
                      "removed", "line_status", "problem"):
                if getattr(cur, k) != getattr(fresh, k):
                    setattr(cur, k, getattr(fresh, k))
        for no, cur in existing.items():
            if no not in seen and not cur.removed:
                cur.removed, cur.line_status = True, "Cancelled"
        if after_940:  # FR-OB-16 / AC-OB-18 / AC-OB-70: compare with the 940's locked values
            held = {i.item_code for i in (await self.s.execute(select(Item).where(
                Item.item_code.in_({p.item_code for p in plans if p.item_code}), Item.warehouse_held.is_(True)))).scalars()}
            now_lines = {str(p.line.line_no): {"item_code": p.item_code, "qty_ea": p.qty_ea}
                         for p in plans if p.item_code in held and p.qty_ea and not p.line.excluded}
            await changed_after_940(self.uow, self.tasks, order, now_lines, h.get("ship_to", order.ship_to))
            return
        order.review_reason = "\n".join(reasons) or None
        paid = self._paid(partner, src.source_type, h.get("shopify_financial_status"))
        if paid and order.paid_at is None and partner is not None and partner.settlement_mode == "PREPAID":
            order.paid_at = received_at  # D20: the delay runs from here, never restarted
        status = F(order.fulfillment_status)
        if reasons and status in (F.PENDING, F.PAYMENT_HOLD):
            self._to(order, F.REVIEW, "source update needs review (FR-OB-15/16)")
            order.hold_reason = None
            await self._raise_review(order, reasons)
        elif status == F.PAYMENT_HOLD and paid:
            self._to(order, F.PENDING, "payment received (FR-OB-12)")
            order.hold_reason = None
        elif status == F.REVIEW and reasons:
            await self._raise_review(order, reasons)  # refresh what the task says
        await self._archive_and_cancel(order, src, partner, paid)

    async def _archive_and_cancel(self, order: Order, src: SourceOrder, partner, paid: bool) -> None:
        h = src.header
        status = F(order.fulfillment_status)
        if h.get("source_cancelled_at") and status in UPDATABLE - {F.DRAFT}:
            order.source_cancelled_at = h["source_cancelled_at"]
            self._to(order, F.CANCELLED, "cancelled at source before the 940 (FR-OB-18)")
            await self._resolve(ORDER_ARCHIVED, order)
            await self._resolve(ORDER_REVIEW, order)
            return
        if src.source_type != "SHOPIFY":
            return
        archived_at = h.get("archived_in_shopify_at")
        order.archived_in_shopify_at = archived_at
        before_940 = status in (F.REVIEW, F.PAYMENT_HOLD, F.PENDING, F.ARCHIVE_SUPPRESSED)
        if before_940:
            order.archive_suppressed = bool(archived_at)  # the suppression is recorded on the order (FR-OB-03d)
        if archived_at and status == F.PENDING:
            self._to(order, F.ARCHIVE_SUPPRESSED, "archived in Shopify before any 940 (FR-OB-03d)")
            if paid and not order.historical:
                await self.tasks.raise_problem(self.s, ORDER_ARCHIVED, order.id, problem(
                    ORDER_ARCHIVED, f"Archived in Shopify before shipping: order {order.label}",
                    f"Order {order.label}, paid "
                    f"{order.total} {order.currency}, was archived in Shopify at {archived_at.isoformat()} "
                    "before any 940. Confirm the archive was intended, or un-archive it in Shopify."))
        elif not archived_at and status == F.ARCHIVE_SUPPRESSED:
            self._to(order, F.PENDING, "un-archived in Shopify (FR-OB-03d)")
            await self._resolve(ORDER_ARCHIVED, order)
        elif status == F.ARCHIVE_SUPPRESSED and not paid:
            await self._resolve(ORDER_ARCHIVED, order)  # refunded or voided: the condition is gone

    async def _cancelled_after_940(self, order: Order, h: dict) -> bool:
        """FR-OB-18 / AC-OB-40: cancelled at source after the 940 was made. Not
        sent yet → cancelled with it. Sent, accepted or shipped → On Hold and
        one task stating what shipped ("nothing shipped" reads differently from
        "N EA shipped"). The email to the warehouse and the owner is P8."""
        status = F(order.fulfillment_status)
        if status in (F.GENERATED_940, F.REJECTED_940, F.FAILED_940):  # nothing reached the warehouse
            order.source_cancelled_at = h["source_cancelled_at"]
            self._to(order, F.CANCELLED, "cancelled at source; no 940 reached the warehouse (FR-OB-18)")
            return True
        if status not in (F.SENT_940, F.ACCEPTED_940, F.TIMEOUT_940, F.SHIPPED):
            return False
        order.source_cancelled_at = h["source_cancelled_at"]
        self._to(order, F.ON_HOLD, "cancelled at source after the 940 (FR-OB-18)")
        lines = list((await self.s.execute(select(OrderLine).where(OrderLine.order_id == order.id,
                                                                   OrderLine.removed.is_(False)))).scalars())
        shipped = sum(int(ln.shipped_ea or 0) for ln in lines)
        what = ("Nothing has shipped yet: tell the warehouse not to ship it." if shipped == 0 else
                f"{shipped} EA already shipped (" + ", ".join(f"{ln.item_code} {ln.shipped_ea}" for ln in lines if ln.shipped_ea)
                + "): decide on a return or a credit.")
        await self.tasks.raise_problem(self.s, CANCELLED_AFTER_940, order.id, problem(
            CANCELLED_AFTER_940, f"Cancelled after the 940: order {order.label}",
            f"Order {order.label} was cancelled at source "
            f"after its 940. {what} The order is On Hold.", urgent=True))
        return True

    async def _raise_review(self, order: Order, reasons: list[str]) -> None:
        await self.tasks.raise_problem(self.s, ORDER_REVIEW, order.id, problem(
            ORDER_REVIEW, f"Order {order.label} needs review",
            f"Order {order.label}:\n" + "\n".join(reasons)))

    async def _resolve(self, event: str, order: Order) -> None:
        await self.tasks.resolve_problem(self.s, event, order.id, by=self.uow.actor.user_id)

    # ── review resolution (§6 resolve-review) ────────────────────────────────
    async def recheck(self, order: Order, barcodes: BarcodeLookup | None = None) -> list[str]:
        """Re-match the lines against today's Item Master. Clean → Payment Hold
        (PREPAID, still unpaid) or Pending, and the task completes."""
        catalog = await Catalog.load(self.s)
        partner = (await self.s.execute(select(Partner).where(Partner.code == order.partner_code))).scalar_one_or_none()
        lines = list((await self.s.execute(select(OrderLine).where(
            OrderLine.order_id == order.id).order_by(OrderLine.line_no))).scalars())
        src = SourceOrder(order.source_type, order.partner_code, order.source_order_id, order.source_order_number,
                          {"ship_to": order.ship_to}, [
                              SourceLine(ln.line_no, Decimal(ln.ordered_qty),
                                         ln.order_unit if order.source_type != "SHOPIFY" else None,
                                         {k: v for k, v in ln.source.items() if k != "inner_packs"},
                                         inner_packs=Decimal(ln.source["inner_packs"]) if "inner_packs" in ln.source else None,
                                         excluded=ln.problem if ln.removed else None)
                              for ln in lines])
        await add_barcodes(src, catalog, barcodes)
        plans = plan_lines(src, catalog)
        reasons = review_reasons(src, partner, plans, catalog)
        by_no = {ln.line_no: ln for ln in lines}
        for p in plans:
            fresh = self._line(order, p)
            cur = by_no[p.line.line_no]
            for k in ("item_code", "source", "order_unit", "factor_ea", "qty_ea", "problem"):
                setattr(cur, k, getattr(fresh, k))
        order.review_reason = "\n".join(reasons) or None
        if not reasons and order.fulfillment_status == F.REVIEW:
            paid = self._paid(partner, order.source_type, order.shopify_financial_status)
            if partner is not None and partner.settlement_mode == "PREPAID" and not paid:
                self._to(order, F.PAYMENT_HOLD, "review resolved; payment still required (FR-OB-12)")
                order.hold_reason = "Payment Required"
            else:
                self._to(order, F.PENDING, "review resolved")
            await self._resolve(ORDER_REVIEW, order)
        await self.s.flush()
        return reasons


def line_view(ln: OrderLine) -> dict[str, Any]:
    return {k: getattr(ln, k) for k in ("line_no", "item_code", "ordered_qty", "order_unit", "qty_ea", "problem")}
