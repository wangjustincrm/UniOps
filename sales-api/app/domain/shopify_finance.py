"""A Shopify order's finance (user 2026-11-03): its payments, refunds and the
summary — read from the order JSON (refunds, tax lines, discounts, the sales
channel, tags) and its transactions (orders/{id}/transactions.json). Pure:
JSON in, values out. Card data never reaches here (the gateway strips it).

Amounts are the shop's currency as Shopify gives them. `net` = the original
total − the successful refund transactions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

CENT = Decimal("0.01")


def _dec(v: Any) -> Decimal:
    try:
        return Decimal(str(v)).quantize(CENT) if v not in (None, "") else Decimal("0.00")
    except Exception:  # noqa: BLE001 — a malformed amount counts as nothing
        return Decimal("0.00")


def _ts(v: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def _int(v: Any) -> int | None:
    return int(v) if str(v or "").isdigit() else None


@dataclass
class Payment:
    shopify_id: int
    parent_id: int | None
    kind: str
    gateway: str | None
    status: str | None
    amount: Decimal
    currency: str | None
    processed_at: datetime | None
    authorization: str | None
    payment_id: str | None
    error_code: str | None
    source_name: str | None
    test: bool
    refund_shopify_id: int | None
    raw: dict[str, Any]


@dataclass
class RefundLine:
    shopify_id: int
    line_item_id: int | None
    sku: str | None
    title: str | None
    quantity: int
    subtotal: Decimal
    total_tax: Decimal
    restock_type: str | None


@dataclass
class Adjustment:
    shopify_id: int
    kind: str | None
    reason: str | None
    amount: Decimal
    tax_amount: Decimal


@dataclass
class Refund:
    shopify_id: int
    refunded_at: datetime | None
    processed_at: datetime | None
    note: str | None
    restock: bool | None
    lines: list[RefundLine]
    adjustments: list[Adjustment]
    amount: Decimal               # its successful refund transactions
    raw: dict[str, Any]

    @property
    def lines_amount(self) -> Decimal:
        return sum((ln.subtotal + ln.total_tax for ln in self.lines), Decimal("0.00"))

    @property
    def shipping_amount(self) -> Decimal:  # Shopify gives a shipping refund as a negative adjustment
        return -sum((a.amount + a.tax_amount for a in self.adjustments if a.kind == "shipping_refund"), Decimal("0.00"))

    @property
    def adjustments_amount(self) -> Decimal:
        return -sum((a.amount + a.tax_amount for a in self.adjustments if a.kind != "shipping_refund"), Decimal("0.00"))


@dataclass
class LineFinance:
    tax_amount: Decimal
    discount_amount: Decimal
    tax_lines: list[dict[str, Any]]


@dataclass
class Finance:
    source_name: str | None
    tags: str | None
    payment_gateways: list[str]
    discount_codes: list[dict[str, Any]]
    tax_lines: list[dict[str, Any]]
    original_total: Decimal
    total_refunded: Decimal
    net_amount: Decimal
    lines: dict[int, LineFinance] = field(default_factory=dict)  # by Shopify line item id (= order line_no)
    payments: list[Payment] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)


def _tax_lines(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [{"title": t.get("title"), "rate": t.get("rate"), "amount": str(_dec(t.get("price")))} for t in items or []]


def payment_of(t: dict[str, Any], refund_shopify_id: int | None = None) -> Payment | None:
    sid = _int(t.get("id"))
    if sid is None:
        return None
    receipt_id = t.get("payment_id")
    return Payment(
        shopify_id=sid, parent_id=_int(t.get("parent_id")), kind=str(t.get("kind") or "")[:20] or "unknown",
        gateway=(t.get("gateway") or None) and str(t["gateway"])[:80], status=(t.get("status") or None) and str(t["status"])[:20],
        amount=_dec(t.get("amount")), currency=(t.get("currency") or None) and str(t["currency"])[:3],
        processed_at=_ts(t.get("processed_at") or t.get("created_at")),
        authorization=(t.get("authorization") or None) and str(t["authorization"])[:255],
        payment_id=(receipt_id or None) and str(receipt_id)[:100],
        error_code=(t.get("error_code") or None) and str(t["error_code"])[:60],
        source_name=(t.get("source_name") or None) and str(t["source_name"])[:60], test=bool(t.get("test")),
        refund_shopify_id=refund_shopify_id or _int(t.get("refund_id")), raw=t)


def refunded(payments: list[Payment]) -> Decimal:
    """The successful refund transactions."""
    return sum((p.amount for p in payments if p.kind == "refund" and p.status == "success"), Decimal("0.00"))


def finance_of(o: dict[str, Any], transactions: list[dict[str, Any]]) -> Finance:
    """The order JSON (with `refunds`) and its transactions → the finance to store."""
    refunds: list[Refund] = []
    payments: dict[int, Payment] = {}
    for r in o.get("refunds") or []:
        rid = _int(r.get("id"))
        if rid is None:
            continue
        txs = [p for p in (payment_of(t, rid) for t in r.get("transactions") or []) if p]
        for p in txs:
            payments[p.shopify_id] = p
        refunds.append(Refund(
            shopify_id=rid, refunded_at=_ts(r.get("created_at")), processed_at=_ts(r.get("processed_at")),
            note=r.get("note") or None, restock=r.get("restock"),
            lines=[RefundLine(
                shopify_id=_int(rl.get("id")) or 0, line_item_id=_int(rl.get("line_item_id")),
                sku=((rl.get("line_item") or {}).get("sku") or None),
                title=((rl.get("line_item") or {}).get("title") or (rl.get("line_item") or {}).get("name") or None),
                quantity=int(rl.get("quantity") or 0), subtotal=_dec(rl.get("subtotal")), total_tax=_dec(rl.get("total_tax")),
                restock_type=(rl.get("restock_type") or None)) for rl in r.get("refund_line_items") or [] if _int(rl.get("id"))],
            adjustments=[Adjustment(
                shopify_id=_int(a.get("id")) or 0, kind=a.get("kind") or None, reason=a.get("reason") or None,
                amount=_dec(a.get("amount")), tax_amount=_dec(a.get("tax_amount"))) for a in r.get("order_adjustments") or []
                if _int(a.get("id"))],
            amount=refunded(txs), raw=r))
    for t in transactions:  # the order's own list: every kind; a refund's keeps its refund
        p = payment_of(t)
        if p is not None:
            known = payments.get(p.shopify_id)
            p.refund_shopify_id = p.refund_shopify_id or (known.refund_shopify_id if known else None)
            payments[p.shopify_id] = p
    paid = list(payments.values())
    original = _dec(o.get("total_price"))
    total_refunded = refunded(paid)
    lines = {}
    for li in o.get("line_items") or []:
        lid = _int(li.get("id"))
        if lid is None:
            continue
        tl = _tax_lines(li.get("tax_lines"))
        lines[lid] = LineFinance(
            tax_amount=sum((Decimal(t["amount"]) for t in tl), Decimal("0.00")),
            discount_amount=sum((_dec(d.get("amount")) for d in li.get("discount_allocations") or []), Decimal("0.00")),
            tax_lines=tl)
    return Finance(
        source_name=(o.get("source_name") or None) and str(o["source_name"])[:60],
        tags=o.get("tags") or None,
        payment_gateways=[str(g) for g in o.get("payment_gateway_names") or []],
        discount_codes=[{"code": d.get("code"), "type": d.get("type"), "amount": str(_dec(d.get("amount")))}
                        for d in o.get("discount_codes") or []],
        tax_lines=_tax_lines(o.get("tax_lines")),
        original_total=original, total_refunded=total_refunded, net_amount=original - total_refunded,
        lines=lines, payments=sorted(paid, key=lambda p: p.shopify_id),
        refunds=refunds)
