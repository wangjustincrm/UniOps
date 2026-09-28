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


def _pa_payment(subtotal: Decimal, tax: Decimal, row) -> Decimal:
    shipping = Decimal(str(getattr(row, "shipping_amount", 0) or 0))
    other = Decimal(str(getattr(row, "other_charges", 0) or 0))
    prepay = Decimal(str(getattr(row, "prepayment_applied", 0) or 0))
    return _q(subtotal + tax + shipping + other - prepay)


def _pa(row, lines):
    subtotal = _sum_lines(lines)
    rate = Decimal(str(getattr(row, "tax_rate", 0) or 0))
    tax = _q(subtotal * rate)
    return {"subtotal": subtotal, "tax_amount": tax, "payment_amount": _pa_payment(subtotal, tax, row)}


# Header fields that feed the PA totals, besides the lines themselves.
PA_CHARGE_FIELDS = ("tax_rate", "shipping_amount", "other_charges", "prepayment_applied")


def recompute_pa_header_only(row, *, rate_changed: bool) -> dict:
    """Totals for a PA that has NO line items, whose stored subtotal stands in
    for them. Tax is the subtotal × rate (shipping and other charges are never
    taxed — taxable freight goes on a line), but it is only re-derived when the
    rate itself changed: lineless PAs are agreement/migrated rows whose
    tax_amount was entered by hand, often with no rate at all, and a shipping
    edit must not zero it. No floor at zero — credit PAs are negative."""
    subtotal = Decimal(str(getattr(row, "subtotal", 0) or 0))
    if rate_changed:
        tax = _q(subtotal * Decimal(str(getattr(row, "tax_rate", 0) or 0)))
    else:
        tax = Decimal(str(getattr(row, "tax_amount", 0) or 0))
    return {"tax_amount": tax, "payment_amount": _pa_payment(subtotal, tax, row)}


_RECOMPUTE = {"pr": _pr, "po": _po, "pa": _pa}


def recompute_header(entity: str, row, lines: list[dict]) -> dict:
    fn = _RECOMPUTE.get(entity)
    return fn(row, lines) if fn else {}
