"""Which gateway transaction is which Shopify payment (Finance, user 2026-11-17). Pure: no database.

Strongest rule first, over all transactions, so a weak rule never takes a payment a strong one
would have matched. Each payment and each transaction is matched at most once.

1. TRANSACTION_ID — Shopify has the gateway's own id: the old Authorize.Net gateway's
   `authorization` ("121004214042#2281#purchase" → 121004214042).
2. SHOPIFY_PAYMENT_ID — the gateway transaction carries Shopify's payment id (Shopify's
   Authorize.Net app, from 2025-09-18) in its invoice number or description.
3. ORDER_NUMBER — the invoice number or description names the Shopify order number, with the
   same kind and amount, and only one such payment.
4. AMOUNT_TIME — same kind, same amount, within `WINDOW`, and the only candidate both ways.
   A probable match only: the Payments list says so. Tried only for a transaction marked `final`
   (its details were read), so a stronger rule from the details is never pre-empted.

Settled money is matched: a settled sale to a successful Shopify sale / capture, a settled refund
to a successful Shopify refund. A voided transaction is matched to the Shopify sale it voided (the
caller passes it as kind "sale"): Shopify records such a void as a sale plus a refund, and the
Payments list then shows both as voided in the gateway, not as money. Declined transactions stay
unmatched and are not an issue.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

WINDOW = timedelta(minutes=15)
MIN_PAYMENT_ID = 8  # a Shopify payment id is ~25 characters; a shorter one is not searched for
_TOKEN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class GatewayTx:
    key: int
    kind: str                     # sale | refund
    amount: Decimal
    submitted_at: datetime | None
    texts: tuple[str, ...]        # invoice number, description, …
    external_id: str
    # Amount and time are tried only once nothing better can come: its details were read.
    final: bool = True


@dataclass(frozen=True)
class ShopifyPayment:
    id: int
    kind: str                     # sale | refund (a capture counts as a sale)
    amount: Decimal
    processed_at: datetime | None
    gateway_id: str | None        # the processor's id, where Shopify has it
    payment_id: str | None
    order_number: str | None


@dataclass(frozen=True)
class Match:
    tx: int
    payment: int
    method: str


def _number_in(number: str, text: str) -> bool:
    return bool(number) and re.search(rf"(?<!\d){re.escape(number)}(?!\d)", text) is not None


def match(txs: list[GatewayTx], payments: list[ShopifyPayment], *, window: timedelta = WINDOW) -> list[Match]:
    out: list[Match] = []
    used_tx: set[int] = set()
    used_p: set[int] = set()

    def take(t: GatewayTx, p: ShopifyPayment, method: str) -> None:
        out.append(Match(t.key, p.id, method))
        used_tx.add(t.key)
        used_p.add(p.id)

    # 1. the gateway's id (a sale and its refund may share it: keyed by both)
    by_gid = {(p.gateway_id, p.kind): p for p in payments if p.gateway_id}
    for t in txs:
        p = by_gid.get((t.external_id, t.kind))
        if p is not None and p.id not in used_p:
            take(t, p, "TRANSACTION_ID")

    # 2. Shopify's payment id in the transaction's text (a whole token: ids are letters and digits)
    by_pid = {p.payment_id: p for p in payments if p.payment_id and len(p.payment_id) >= MIN_PAYMENT_ID}
    for t in txs:
        if t.key in used_tx:
            continue
        for token in (tok for x in t.texts if x for tok in _TOKEN.findall(x)):
            hit = by_pid.get(token)
            if hit is not None and hit.id not in used_p and hit.kind == t.kind:
                take(t, hit, "SHOPIFY_PAYMENT_ID")
                break

    # 3. the order number with the same kind and amount, one payment only
    by_amount: dict[tuple[str, Decimal], list[ShopifyPayment]] = defaultdict(list)
    for p in payments:
        by_amount[(p.kind, p.amount)].append(p)
    for t in txs:
        if t.key in used_tx or not any(t.texts):
            continue
        found = [p for p in by_amount.get((t.kind, t.amount), []) if p.id not in used_p and p.order_number
                 and any(_number_in(p.order_number, x) for x in t.texts if x)]
        if len(found) == 1:
            take(t, found[0], "ORDER_NUMBER")

    # 4. amount and time, the only candidate both ways
    open_t = [t for t in txs if t.key not in used_tx and t.final and t.submitted_at is not None]
    near_t: dict[int, list[GatewayTx]] = defaultdict(list)
    near_p: dict[int, list[ShopifyPayment]] = {}
    for t in open_t:
        cands = [p for p in by_amount.get((t.kind, t.amount), []) if p.id not in used_p and p.processed_at is not None
                 and abs(p.processed_at - t.submitted_at) <= window]
        near_p[t.key] = cands
        for p in cands:
            near_t[p.id].append(t)
    for t in open_t:
        cands = near_p[t.key]
        if len(cands) == 1 and len(near_t[cands[0].id]) == 1 and cands[0].id not in used_p:
            take(t, cands[0], "AMOUNT_TIME")
    return out
