"""Remittance preview / send — two scopes, one implementation.

A `batch` scope is every completed payment record sharing a batch_id; a
`payment` scope is one record. Both resolve through the same
`app.crud.remittance` grouping and the same `app.crud.remittance_send` send
path — that shared code path is the point of the design, not an incidental
overlap.

Preview is computed live on every call — it never reads cached block state,
so a vendor email filled in after the payment, or an invoice number attached
later, shows up immediately. The frontend's Refresh button is just a re-fetch
of this endpoint.

Mounted onto the payments router. Route order matters: the payment-scoped
paths (`/{payment_id}/remittance/...`) must be registered before payments.py's
catch-all `GET /{payment_id}`, declared at the very bottom of that file.
"""
import uuid
from datetime import date

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud import payment_execute
from app.crud import remittance as rem
from app.crud import remittance_send as rsend
from app.crud.payment_execute import PaymentPermissionError
from app.db.base import get_db
from app.models.payment import PaymentRecord
from app.models.payment_batch import EXECUTED, PaymentBatch
from app.models.remittance import SCOPE_BATCH, SCOPE_PAYMENT, RemittanceNotification
from app.services import remittance_config as rc

router = APIRouter(tags=["payments"])


class RecipientRef(BaseModel):
    recipient_kind: str
    party_id: uuid.UUID


class SendRequest(BaseModel):
    recipients: list[RecipientRef] | None = None


async def _authorize(db: AsyncSession, user: dict) -> None:
    try:
        await payment_execute._check_can_pay(db, user)
    except PaymentPermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


async def _scope_context(db: AsyncSession, *, scope_kind: str,
                          scope_id: uuid.UUID) -> tuple[str, str, date]:
    """(reference, payment_method, payment_date) for the scope, or 404/409."""
    if scope_kind == SCOPE_BATCH:
        batch = (await db.execute(
            select(PaymentBatch).where(PaymentBatch.id == scope_id))).scalar_one_or_none()
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found")
        if batch.status != EXECUTED:
            raise HTTPException(status_code=409, detail=f"Batch is {batch.status}")
        return batch.batch_number, batch.payment_method, batch.batch_date
    rec = (await db.execute(
        select(PaymentRecord).where(PaymentRecord.id == scope_id))).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="Payment record not found")
    if rec.status != rem.COMPLETED:
        raise HTTPException(status_code=409, detail=f"Payment is {rec.status}")
    return (rec.doc_number or rec.pa_number or str(rec.id)), rec.payment_method, rec.payment_date


async def _company_name(db: AsyncSession) -> str:
    """finance-api's `CompanyConfig` mirror deliberately maps only
    `role_management` / `remittance_config` (see app/models/mirrors.py) — it
    does not map `name`, so `CompanyConfig.__table__.c.name` does not exist on
    that ORM Table. Read the physical column with raw SQL instead, the same
    technique `app/services/remittance_config.py` uses for the SMTP columns.

    Checked via information_schema first rather than SELECT-then-catch: the
    finance-api test schema builds `company_config` from the mirror's own
    columns only (see tests/conftest.py's `_migrate`), so in tests — and in
    any deployment where epms-api's base migration hasn't run — the column is
    genuinely absent, not just NULL. A raw `SELECT name ...` in that case
    raises UndefinedColumn and aborts the whole async transaction; checking
    first keeps this a plain, side-effect-free fallback to "UniOps" instead.
    """
    has_col = (await db.execute(sa.text(
        "SELECT 1 FROM information_schema.columns"
        " WHERE table_name = 'company_config' AND column_name = 'name'"
    ))).scalar_one_or_none()
    if not has_col:
        return "UniOps"
    name = (await db.execute(sa.text(
        "SELECT name FROM company_config LIMIT 1"))).scalar_one_or_none()
    return name or "UniOps"


async def _last_sends(db: AsyncSession, scope_kind: str,
                       scope_id: uuid.UUID) -> dict[tuple[str, uuid.UUID], dict]:
    rows = (await db.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_kind == scope_kind,
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    return {(r.recipient_kind, r.party_id): {
        "status": r.status, "error": r.error, "attempts": r.attempts,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None} for r in rows}


async def _preview(db: AsyncSession, user: dict, *, scope_kind: str,
                    scope_id: uuid.UUID) -> dict:
    await _authorize(db, user)
    reference, method, _ = await _scope_context(db, scope_kind=scope_kind, scope_id=scope_id)
    records = await rem.resolve_scope(db, scope_kind, scope_id)
    groups = await rem.build_groups(db, records)
    last = await _last_sends(db, scope_kind, scope_id)
    settings_ = await rc.load(db)
    return {
        "enabled": settings_ is not None,
        "reference": reference,
        "payment_method": method,
        "groups": [{
            "recipient_kind": g.recipient_kind,
            "party_id": str(g.party_id),
            "party_name": g.party_name,
            "email": g.email,
            "currency": g.currency,
            "total": str(g.total),
            "block_reasons": g.block_reasons,
            "lines": [{
                "reference": (l.vendor_inv_no if g.recipient_kind == "vendor"
                              else l.doc_number),
                "payment_date": l.payment_date.isoformat(),
                "amount": str(l.amount),
            } for l in g.lines],
            "last_send": last.get((g.recipient_kind, g.party_id)),
        } for g in groups],
    }


async def _send(db: AsyncSession, user: dict, body: SendRequest, *,
                 scope_kind: str, scope_id: uuid.UUID) -> dict:
    await _authorize(db, user)
    reference, method, _ = await _scope_context(db, scope_kind=scope_kind, scope_id=scope_id)
    sender = await rc.load(db)
    if sender is None:
        raise HTTPException(status_code=409,
                             detail="Remittance email is not configured or is switched off")
    groups = await rem.build_groups(db, await rem.resolve_scope(db, scope_kind, scope_id))
    if body.recipients is not None:
        # Blocked payees are refused inside send_groups regardless of what the
        # client asks for — this filter only narrows *which* groups are
        # attempted, it never widens or waives a block. A client posting a
        # blocked payee's (recipient_kind, party_id) still gets `skipped`,
        # never `sent`.
        wanted = {(r.recipient_kind, r.party_id) for r in body.recipients}
        groups = [g for g in groups if (g.recipient_kind, g.party_id) in wanted]

    company_name = await _company_name(db)
    results = await rsend.send_groups(
        db, scope_kind=scope_kind, scope_id=scope_id, groups=groups,
        reference=reference, payment_method=method,
        company_name=company_name, sender=sender,
        actor_id=uuid.UUID(str(user["sub"])),
    )
    # No trailing db.commit() here — app/crud/remittance_send.py's
    # send_groups() already commits the log row right after EACH payee's send
    # attempt (see its module docstring). A trailing commit at this level
    # would (a) be a no-op for everything send_groups already committed, and
    # (b) misleadingly imply the rows were still pending on an uncommitted
    # transaction that this handler might never reach — e.g. if something
    # between send_groups returning and here raised, the sends would still be
    # durable; a reader who saw a trailing commit here could wrongly assume
    # the opposite.
    return {
        "sent": sum(1 for r in results if r["status"] == "sent"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "results": results,
    }


@router.get("/batches/{batch_id}/remittance/preview")
async def preview_batch(batch_id: uuid.UUID, user: CurrentUser,
                         db: AsyncSession = Depends(get_db)):
    return await _preview(db, user, scope_kind=SCOPE_BATCH, scope_id=batch_id)


@router.post("/batches/{batch_id}/remittance/send")
async def send_batch(batch_id: uuid.UUID, user: CurrentUser,
                      body: SendRequest = SendRequest(),
                      db: AsyncSession = Depends(get_db)):
    return await _send(db, user, body, scope_kind=SCOPE_BATCH, scope_id=batch_id)


@router.get("/{payment_id}/remittance/preview")
async def preview_payment(payment_id: uuid.UUID, user: CurrentUser,
                           db: AsyncSession = Depends(get_db)):
    return await _preview(db, user, scope_kind=SCOPE_PAYMENT, scope_id=payment_id)


@router.post("/{payment_id}/remittance/send")
async def send_payment(payment_id: uuid.UUID, user: CurrentUser,
                        body: SendRequest = SendRequest(),
                        db: AsyncSession = Depends(get_db)):
    return await _send(db, user, body, scope_kind=SCOPE_PAYMENT, scope_id=payment_id)
