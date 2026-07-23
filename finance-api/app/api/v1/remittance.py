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
from app.crud import payment_batch as batch_crud
from app.crud import payment_execute
from app.crud import remittance as rem
from app.crud import remittance_send as rsend
from app.crud.payment_execute import PaymentPermissionError
from app.db.base import get_db
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord
from app.models.payment_batch import EXECUTED, PaymentBatch
from app.models.remittance import SCOPE_BATCH, SCOPE_PAYMENT
from app.services import remittance_config as rc

router = APIRouter(tags=["payments"])


class RecipientRef(BaseModel):
    recipient_kind: str
    party_id: uuid.UUID
    # Fix 3 (Round 2 correction): resending is a deliberate, supported
    # operation, not the default — but it is a per-PAYEE decision, not a
    # per-REQUEST one. This used to live on SendRequest as a single flag
    # applied to every recipient in the request; a batch send covering ACME
    # (deliberately re-checked for resend) and BOREAL (merely unsent in this
    # scope, sent under the other) folded both under one boolean, so
    # deliberately resending ACME silently waived the duplicate guard for
    # BOREAL too. False refuses (as `skipped`) THIS payee if it is already
    # `sent` for the effective scope (app.crud.remittance.last_send_for_group's
    # cross-scope lookup) — enforced inside app/crud/remittance_send.py's
    # send_groups(), the same place block_reasons are enforced, not just here.
    resend: bool = False


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
    return await _reference_for(db, rec), rec.payment_method, rec.payment_date


async def _reference_for(db: AsyncSession, rec: PaymentRecord) -> str:
    """The `reference` a single payment's remittance email shows in its
    subject and footer (remittance_template.py) — never the PA number
    (spec §3: internal document numbers are meaningless, and mean nothing
    good, to the vendor). The batch path already avoids this by using the
    batch number; here, `doc_number` IS `pa_number` for a vendor payment
    (payment_execute.execute sets `doc_number=pa.pa_number` on every PA
    record), so `rec.doc_number or rec.pa_number` used to resolve to the PA
    number every time for a vendor payee regardless of which field "won".

    A `payment` scope always covers exactly one PaymentRecord
    (resolve_scope), so for a vendor payee this is always exactly one PA —
    unlike a batch, which can span several, there is no ambiguity in using
    that PA's own vendor invoice number as the reference instead: it is the
    one identifier that is *also* meaningful to the vendor, not just
    "internal but at least not the PA number". Falls back to an opaque,
    PA-number-free payment reference only when the PA carries no invoice
    number at all (a Direct PA in that state is blocked from Send anyway —
    see app.crud.remittance.BLOCK_MISSING_INVOICE_NO — so this only matters
    for a still-blocked preview). Employee (expense_claim) payments are
    unaffected: the claim number is already the spec-sanctioned, meaningful
    reference for an employee (see remittance_template.py).
    """
    if rec.doc_kind in ("pa", "pa_dir") and rec.doc_id:
        pa = (await db.execute(
            select(PaymentApplication).where(PaymentApplication.id == rec.doc_id)
        )).scalar_one_or_none()
        if pa is not None:
            inv_no = (await batch_crud.vendor_inv_no_map(db, [pa])).get(pa.id, "")
            if inv_no:
                return inv_no
        return f"PMT-{rec.id.hex[:8].upper()}"
    return rec.doc_number or str(rec.id)


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


async def _preview(db: AsyncSession, user: dict, *, scope_kind: str,
                    scope_id: uuid.UUID) -> dict:
    await _authorize(db, user)
    reference, method, _ = await _scope_context(db, scope_kind=scope_kind, scope_id=scope_id)
    records = await rem.resolve_scope(db, scope_kind, scope_id)
    groups = await rem.build_groups(db, records)
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
            # Fix 2: cross-scope-aware — see rem.last_send_for_group's
            # docstring for why a same-scope-only lookup lies for a payment
            # that was actually sent under its batch (or vice versa).
            "last_send": await rem.last_send_for_group(
                db, scope_kind=scope_kind, scope_id=scope_id, group=g),
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
    # Per-payee resend flags, keyed the same way groups are — NOT a single
    # blanket flag (see RecipientRef.resend's docstring for why folding this
    # into one request-level boolean was the bug). Empty whenever recipients
    # is None: that shorthand means "every non-blocked payee found by the
    # preview", which carries no per-payee resend intent of its own.
    resend_ids: set[tuple[str, uuid.UUID]] = set()
    if body.recipients is not None:
        # Blocked payees are refused inside send_groups regardless of what the
        # client asks for — this filter only narrows *which* groups are
        # attempted, it never widens or waives a block. A client posting a
        # blocked payee's (recipient_kind, party_id) still gets `skipped`,
        # never `sent`.
        wanted = {(r.recipient_kind, r.party_id) for r in body.recipients}
        resend_ids = {(r.recipient_kind, r.party_id) for r in body.recipients if r.resend}
        groups = [g for g in groups if (g.recipient_kind, g.party_id) in wanted]

    company_name = await _company_name(db)
    results = await rsend.send_groups(
        db, scope_kind=scope_kind, scope_id=scope_id, groups=groups,
        reference=reference, payment_method=method,
        company_name=company_name, sender=sender,
        actor_id=uuid.UUID(str(user["sub"])),
        resend_ids=resend_ids,
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
