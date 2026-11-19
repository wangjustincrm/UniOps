"""PayPal settlements (Finance, user 2026-11-19, option A): PayPal has no daily batch — money stays in the PayPal
balance until it is transferred to the bank — so a settlement is one transfer to the bank (a T04xx event), holding
everything that happened in its currency since the previous transfer. What has not left yet is the "In PayPal
balance" row of that currency.

Per settlement, in the shared columns:
- Sales / Refunds: settled sales and refunds; Fees: what PayPal took from them (a fee given back with a refund
  lowers it); Net: sales − refunds − fees, what they added to the balance;
- Chargebacks: reversals and chargebacks (T1106, T12xx), not in Net;
- raw.transfer_amount: what went to the bank; raw.carried_amount: transfer − Net. It is not forced to zero: a
  transfer of part of the balance, a hold, a deposit from the bank (T0300) funding refunds, or a balance from
  before the first transfer read all make it differ; raw.notes says which other events the period had.

Rebuilt whole from the connection's transactions after every pass (idempotent: a re-read moves nothing twice).
"""
from __future__ import annotations

import collections
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.domain.gateways import paypal_withdrawal
from app.models.gateways import GatewaySettlement, GatewayTransaction
from app.platform.actors import SYSTEM_ACTOR

GATEWAY = "PAYPAL"
IN_BALANCE = "In PayPal balance"
TRANSFERRED = "Transferred to the bank"
# Other events, in words, for a settlement's notes (anything else by its code).
EVENT_WORDS = {"T03": "deposit from the bank", "T1110": "hold", "T1111": "hold released", "T02": "currency conversion",
               "T01": "fee", "T1106": "reversal", "T12": "chargeback", "T21": "hold on the balance", "T22": "transfer"}
CHARGEBACK = ("T1106", "T12")
ZERO = Decimal("0.00")


def _code(t: GatewayTransaction) -> str:
    return str((t.raw or {}).get("transaction_event_code") or "")


def _word(code: str) -> str:
    for prefix in sorted(EVENT_WORDS, key=len, reverse=True):
        if code.startswith(prefix):
            return EVENT_WORDS[prefix]
    return code or "other"


def _is_transfer(t: GatewayTransaction) -> bool:
    return t.kind == "other" and paypal_withdrawal(_code(t)) and (t.status or "").upper() not in ("D", "V")


def figures(txs: list[GatewayTransaction], transfer: Decimal | None) -> dict[str, Any]:
    """A period's figures (its transfer excluded from `txs`)."""
    sales = [t for t in txs if t.kind == "sale" and t.settled]
    refunds = [t for t in txs if t.kind == "refund" and t.settled]
    fees = sum((t.fee_amount or ZERO for t in sales + refunds), ZERO)
    chargebacks = [t for t in txs if t.kind == "other" and _code(t).startswith(CHARGEBACK)]
    net = sum((t.amount for t in sales), ZERO) - sum((t.amount for t in refunds), ZERO) - fees
    events: dict[str, list[Decimal]] = collections.defaultdict(list)
    for t in txs:
        if t.kind == "other" and not _code(t).startswith(CHARGEBACK):
            events[_word(_code(t))].append(t.amount)
    notes = "; ".join(f"{len(v)} × {w} ({sum(v, ZERO):.2f})" for w, v in sorted(events.items()))
    out = {"sales_amount": sum((t.amount for t in sales), ZERO), "sales_count": len(sales),
           "refund_amount": sum((t.amount for t in refunds), ZERO), "refund_count": len(refunds),
           "fee_amount": fees, "net_amount": net,
           "declined_count": sum(1 for t in txs if t.kind == "sale" and (t.status or "").upper() == "D"), "voided_count": 0,
           "chargeback_amount": sum((t.amount for t in chargebacks), ZERO), "chargeback_count": len(chargebacks),
           "returned_amount": ZERO, "returned_count": 0}
    raw = {"notes": notes or None}
    if transfer is not None:
        raw |= {"transfer_amount": f"{transfer:.2f}", "carried_amount": f"{transfer - net:.2f}"}
    return out | {"raw": raw}


async def rebuild(uow_factory, connection_id: int, now: datetime) -> int:
    """Every transfer of the connection a settlement, with its period's transactions; the rest in the balance row.
    Returns the number of transfers."""
    async with uow_factory(SYSTEM_ACTOR, reason="PayPal settlements") as uow:
        s = uow.session
        txs = list((await s.execute(select(GatewayTransaction).where(
            GatewayTransaction.connection_id == connection_id, GatewayTransaction.gateway == GATEWAY)
            .order_by(GatewayTransaction.submitted_at, GatewayTransaction.id))).scalars())
        existing = {r.external_id: r for r in (await s.execute(select(GatewaySettlement).where(
            GatewaySettlement.connection_id == connection_id, GatewaySettlement.gateway == GATEWAY))).scalars()}

        def row(ext: str, currency: str, at: datetime) -> GatewaySettlement:
            r = existing.get(ext)
            if r is None:
                r = GatewaySettlement(connection_id=connection_id, gateway=GATEWAY, external_id=ext, currency=currency,
                                      settled_at=at, raw={})
                s.add(r)
                existing[ext] = r
            return r

        transfers = 0
        by_currency: dict[str, list[GatewayTransaction]] = collections.defaultdict(list)
        for t in txs:
            by_currency[t.currency].append(t)
        for currency, items in by_currency.items():
            period: list[GatewayTransaction] = []
            for t in items:
                if not _is_transfer(t):
                    period.append(t)
                    continue
                transfers += 1
                tid = t.external_id.split(":")[0]
                r = row(tid, currency, t.submitted_at or now)
                f = figures(period, t.amount)
                r.raw = f.pop("raw")
                for k, v in f.items():
                    setattr(r, k, v)
                r.settled_at, r.state, r.payment_method = t.submitted_at or now, TRANSFERRED, "Bank transfer"
                r.transactions_read_at = now
                await s.flush()
                for x in period + [t]:
                    x.settlement_id = r.id
                period = []
            # What has not left: the balance row (kept, at zero, once it existed).
            ext = f"BALANCE-{currency}"
            if period or ext in existing:
                r = row(ext, currency, period[-1].submitted_at if period and period[-1].submitted_at else now)
                f = figures(period, None)
                r.raw = f.pop("raw")
                for k, v in f.items():
                    setattr(r, k, v)
                r.settled_at = (period[-1].submitted_at if period and period[-1].submitted_at else r.settled_at)
                r.state, r.payment_method, r.transactions_read_at = IN_BALANCE, None, now
                await s.flush()
                for x in period:
                    x.settlement_id = r.id
        await s.flush()
        await uow.commit()
        return transfers
