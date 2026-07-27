"""Per-entity header recompute from line items. Mirrors the create-path totals in
crud/{pr,po,pa}.py. Returns a dict of header field -> new Decimal; the caller sets
them on the row."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def _q(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _sum_lines(lines: list[dict]) -> Decimal:
    return _q(sum((Decimal(str(li["line_total"])) for li in lines), Decimal("0")))


def _pr(row, lines):
    return {"amount": _sum_lines(lines)}


def _po(row, lines):
    subtotal = _sum_lines(lines)
    rate = Decimal(str(getattr(row, "tax_rate", 0) or 0))
    tax = _q(subtotal * rate)
    return {"subtotal": subtotal, "tax_amount": tax, "total": _q(subtotal + tax)}


def _pa(row, lines):
    subtotal = _sum_lines(lines)
    rate = Decimal(str(getattr(row, "tax_rate", 0) or 0))
    tax = _q(subtotal * rate)
    shipping = Decimal(str(getattr(row, "shipping_amount", 0) or 0))
    other = Decimal(str(getattr(row, "other_charges", 0) or 0))
    prepay = Decimal(str(getattr(row, "prepayment_applied", 0) or 0))
    payment = _q(subtotal + tax + shipping + other - prepay)
    return {"subtotal": subtotal, "tax_amount": tax, "payment_amount": payment}


_RECOMPUTE = {"pr": _pr, "po": _po, "pa": _pa}


def recompute_header(entity: str, row, lines: list[dict]) -> dict:
    fn = _RECOMPUTE.get(entity)
    return fn(row, lines) if fn else {}
