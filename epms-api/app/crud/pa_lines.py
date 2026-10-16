"""PA line items derived from the invoices the PA pays.

A PA's header amount comes from its invoices, but its lines used to be ticked by
hand off the PO's line table — two inputs that nothing tied together. In
production a fifth of PO-route PAs ended up with lines that do not add up to
their own pre-tax amount, most often because every PO line was ticked
(PA-20260924-0006: 22 lines worth 981.98 on a 607.90 payment). The lines are
not cosmetic: Data Maintenance recomputes the header from them, and the PA PDF
prints them.

When the match already recorded which PO line each invoice line bought, the PA's
lines are a function of its invoices and are built here instead. A match made
only against the PO total (no line-level allocation) carries no such
information, so those PAs keep the hand-picked lines.

The same rule runs in the browser to show the lines before saving
(epms/src/lib/paLinesFromInvoices.ts); the server's result is what is stored.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.po import PoLineItem

# PaLineItem.unit is NOT NULL; an invoice line often has no unit at all.
_FALLBACK_UNIT = "ea"


def _dec(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _qty_price(inv_line: dict, amount: Decimal) -> tuple[Decimal, Decimal]:
    """Quantity and unit price as invoiced. line_total is stored separately and
    is always the allocated amount, so a price the OCR rounded cannot move money."""
    qty = _dec(inv_line.get("quantity"))
    price = _dec(inv_line.get("unit_price"))
    if qty is None or qty <= 0:
        return Decimal("1"), amount
    if price is None:
        price = (amount / qty).quantize(Decimal("0.00001"))
    return qty, price


def _note(inv: Invoice) -> str:
    return f"{inv.internal_ref} · {inv.vendor_invoice_number}" if inv.vendor_invoice_number else inv.internal_ref


async def derive_lines(
    db: AsyncSession, invoice_ids: list, po_ids: list[uuid.UUID]
) -> list[dict] | None:
    """The PA lines its invoices imply, or None when they cannot be derived.

    Derivable means every invoice is either matched line by line to the PA's
    POs, or is a fee-only invoice (every line a non-PO fee) headed on one of
    them. One header-level match anywhere returns None — mixing derived lines
    with hand-picked ones would leave the hand-picked half as unchecked as before.

    Per invoice:
    - each allocation to one of the PA's POs becomes a line on that PO line,
      worth exactly the allocated amount (allocations to other POs belong to
      those POs' PAs);
    - each non-PO fee line becomes a line with no PO line, but only on the PA of
      the invoice's header PO, so a fee on a multi-PO invoice is paid once.
    """
    if not invoice_ids or not po_ids:
        return None
    ids = [uuid.UUID(str(i)) for i in invoice_ids]
    by_id = {inv.id: inv for inv in (await db.execute(
        select(Invoice).where(Invoice.id.in_(ids))
    )).scalars().all()}
    if len(by_id) != len(set(ids)):
        return None   # an id that is not an EPMS invoice (OA Direct PA) — nothing to derive from
    po_set = set(po_ids)

    plan: list[tuple[Invoice, list]] = []
    po_line_ids: set[uuid.UUID] = set()
    for iid in ids:
        inv = by_id[iid]
        lines = inv.line_items or []
        mine = [a for a in (inv.allocations or []) if a.po_id in po_set]
        if mine:
            if any(a.po_line_id is None for a in mine):
                return None
            po_line_ids.update(a.po_line_id for a in mine)
        elif not (inv.po_id in po_set and lines and all(li.get("non_po_fee") for li in lines)):
            return None
        plan.append((inv, mine))

    po_lines = {pl.id: pl for pl in (await db.execute(
        select(PoLineItem).where(PoLineItem.id.in_(po_line_ids))
    )).scalars().all()} if po_line_ids else {}

    out: list[dict] = []
    for inv, mine in plan:
        inv_lines = {str(li.get("id")): li for li in (inv.line_items or [])}
        alloc_by_inv_line = {str(a.invoice_line_id): a for a in mine}
        fees_here = inv.po_id in po_set
        # Invoice line order, so the PA reads like the invoice it pays.
        for li in inv.line_items or []:
            lid = str(li.get("id"))
            a = alloc_by_inv_line.pop(lid, None)
            if a is not None:
                pl = po_lines.get(a.po_line_id)
                qty, price = _qty_price(li, a.allocated_amount)
                out.append({
                    "po_line_id": a.po_line_id,
                    "description": (pl.description if pl else li.get("description") or "")[:500] or inv.internal_ref,
                    "qty": qty,
                    "unit": (pl.unit if pl else None) or li.get("unit") or _FALLBACK_UNIT,
                    "unit_price": price,
                    "line_total": a.allocated_amount,
                    "notes": _note(inv),
                })
            elif li.get("non_po_fee") and fees_here:
                amount = _dec(li.get("line_total")) or Decimal("0")
                qty, price = _qty_price(li, amount)
                desc = li.get("description") or "Non-PO fee"
                if li.get("non_po_note"):
                    desc = f"{desc} — {li['non_po_note']}"
                out.append({
                    "po_line_id": None,
                    "description": desc[:500],
                    "qty": qty,
                    "unit": li.get("unit") or _FALLBACK_UNIT,
                    "unit_price": price,
                    "line_total": amount.quantize(Decimal("0.01")),
                    "notes": _note(inv),
                })
        # An allocation whose invoice line is gone from line_items (edited after
        # the match) still pays its PO line; keep it rather than drop money.
        for a in alloc_by_inv_line.values():
            pl = po_lines.get(a.po_line_id)
            out.append({
                "po_line_id": a.po_line_id,
                "description": (pl.description if pl else inv.internal_ref)[:500],
                "qty": Decimal("1"),
                "unit": (pl.unit if pl else None) or _FALLBACK_UNIT,
                "unit_price": a.allocated_amount,
                "line_total": a.allocated_amount,
                "notes": _note(inv),
            })
    return out or None
