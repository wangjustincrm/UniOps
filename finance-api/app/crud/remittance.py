"""Remittance payee grouping.

Anchored on payment_records so a batch run and a single payment share one
code path: a scope resolves to a set of records, records group into payees.
Block reasons are computed live on every call — never cached — so filling in
a vendor email or attaching an invoice number unblocks Send immediately.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.payment_batch import vendor_inv_no_map
from app.models.mirrors import BusinessPartner, ExpenseClaim, User
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord
from app.models.remittance import (
    KIND_EMPLOYEE, KIND_VENDOR, SCOPE_BATCH, SCOPE_PAYMENT, RemittanceNotification,
)

BLOCK_MISSING_EMAIL = "missing_email"
BLOCK_MISSING_INVOICE_NO = "missing_invoice_no"

_VENDOR_KINDS = ("pa", "pa_dir")
COMPLETED = "completed"

# Fixed namespace for deriving a selection's deterministic scope id (Fix:
# selection scope). Any stable UUID works — this one has no other meaning.
_SELECTION_NS = uuid.UUID("6c9b6b1e-6f2d-4e4a-9c1a-6f2f6f2c9b1e")


@dataclass
class GroupLine:
    vendor_inv_no: str
    doc_number: str
    payment_date: date
    amount: Decimal


@dataclass
class PayeeGroup:
    recipient_kind: str
    party_id: uuid.UUID
    party_name: str
    email: str
    currency: str
    lines: list[GroupLine] = field(default_factory=list)
    total: Decimal = Decimal("0")
    block_reasons: list[str] = field(default_factory=list)
    payment_record_ids: list[uuid.UUID] = field(default_factory=list)


async def resolve_scope(db: AsyncSession, scope_kind: str,
                         scope_id: uuid.UUID) -> list[PaymentRecord]:
    """Completed payment records covered by a scope. `batch` = every record
    tagged with the batch; `payment` = that one record."""
    q = select(PaymentRecord).where(PaymentRecord.status == COMPLETED)
    if scope_kind == SCOPE_BATCH:
        q = q.where(PaymentRecord.batch_id == scope_id)
    elif scope_kind == SCOPE_PAYMENT:
        q = q.where(PaymentRecord.id == scope_id)
    else:
        raise ValueError(f"Unknown scope kind '{scope_kind}'")
    return list((await db.execute(q.order_by(PaymentRecord.created_at))).scalars().all())


async def resolve_records(db: AsyncSession, ids: list[uuid.UUID]) -> list[PaymentRecord]:
    """Completed payment records among an arbitrary id list, ordered by
    created_at — the record-resolution half of a `selection` scope. A
    selection is a list of ids, not a single uuid, so it cannot go through
    `resolve_scope` (batch/payment only); this does not invent a scope of its
    own, it just filters+orders the same way resolve_scope's two branches do.
    """
    if not ids:
        return []
    q = select(PaymentRecord).where(
        PaymentRecord.status == COMPLETED, PaymentRecord.id.in_(ids))
    return list((await db.execute(q.order_by(PaymentRecord.created_at))).scalars().all())


def selection_scope_id(ids: list[uuid.UUID]) -> uuid.UUID:
    """Deterministic scope id for a `selection` scope, derived from the
    sorted record ids. Re-sending the exact same set of ids must land on the
    same log row (upsert, `attempts` increments) and a different set must
    land on a different row — both the preview and the send call this same
    helper so they always agree on the id for a given selection.

    Ids are deduplicated first: resolve_records dedupes at the SQL row level,
    so ["a","a","b"] and ["a","b"] cover the identical records and must map to
    the same scope id rather than two different upsert rows.
    """
    key = ",".join(sorted(set(str(i) for i in ids)))
    return uuid.uuid5(_SELECTION_NS, key)


async def last_send_for_group(db: AsyncSession, *, scope_kind: str, scope_id: uuid.UUID,
                               group: "PayeeGroup") -> dict | None:
    """Most recent notification-log entry for one payee, regardless of which
    scope actually performed the send.

    The log's unique key is (scope_kind, scope_id, recipient_kind,
    party_id), so a payment record paid as part of a batch has multiple
    independent identities across scopes: a `batch` row (sent from the batch
    dialog), a `payment` row (sent from the payment drawer), and now a
    `selection` row (sent from an ad-hoc multi-select). Reading only the row
    under the CURRENT scope makes a payee already sent under ANY OTHER scope
    look unsent here — "Ready" and pre-checked in the drawer, one click from
    a duplicate advice. This also resolves the OTHER scopes' rows for the
    same payee via containment on `payment_record_ids` (GIN-indexed — the
    same shape app/api/v1/payments.py's `_remittance_status` already uses
    for its own scope-agnostic check), keyed off THIS group's own
    `payment_record_ids` rather than re-resolving the whole scope. The cross
    lookup filters `scope_kind != scope_kind` rather than enumerating a
    single "other" scope (the old binary `_other_scope(batch|payment)`) — with
    three-plus scopes possible, "any scope but this one" is the general form,
    and it never double-counts the exact-key `same` row because that row is
    always exactly the CURRENT scope. Where both a same-scope and a
    cross-scope row exist, the more recently updated one wins — used both by
    the preview (what to show) and by app/crud/remittance_send.py's send
    guard (what to refuse without an explicit `resend`).
    """
    same = (await db.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_kind == scope_kind,
        RemittanceNotification.scope_id == scope_id,
        RemittanceNotification.recipient_kind == group.recipient_kind,
        RemittanceNotification.party_id == group.party_id,
    ))).scalar_one_or_none()

    cross = None
    ids = [str(i) for i in group.payment_record_ids]
    if ids:
        cross = (await db.execute(sa.text(
            "SELECT status, error, attempts, sent_at, updated_at"
            " FROM payment_remittance_notifications"
            " WHERE scope_kind != :k AND recipient_kind = :rk AND party_id = :pid"
            " AND payment_record_ids ?| :ids"
            " ORDER BY updated_at DESC LIMIT 1"
        ), {"k": scope_kind, "rk": group.recipient_kind,
            "pid": group.party_id, "ids": ids})).mappings().first()

    candidates = []
    if same is not None:
        candidates.append((same.updated_at, same.status, same.error,
                            same.attempts, same.sent_at))
    if cross is not None:
        candidates.append((cross["updated_at"], cross["status"], cross["error"],
                            cross["attempts"], cross["sent_at"]))
    if not candidates:
        return None
    _, status, error, attempts, sent_at = max(candidates, key=lambda c: c[0])
    return {"status": status, "error": error, "attempts": attempts,
            "sent_at": sent_at.isoformat() if sent_at else None}


async def build_groups(db: AsyncSession,
                        records: list[PaymentRecord]) -> list[PayeeGroup]:
    if not records:
        return []
    vendor_recs = [r for r in records if r.doc_kind in _VENDOR_KINDS]
    claim_recs = [r for r in records if r.doc_kind == "expense_claim"]
    groups = await _vendor_groups(db, vendor_recs)
    groups += await _employee_groups(db, claim_recs)
    return groups


async def _vendor_groups(db: AsyncSession,
                          records: list[PaymentRecord]) -> list[PayeeGroup]:
    if not records:
        return []
    pa_ids = [r.doc_id for r in records if r.doc_id]
    pas = (await db.execute(
        select(PaymentApplication).where(PaymentApplication.id.in_(pa_ids))
    )).scalars().all()
    pa_by_id = {p.id: p for p in pas}
    inv_no = await vendor_inv_no_map(db, list(pas))

    vendor_ids = {p.vendor_id for p in pas}
    partners = (await db.execute(
        select(BusinessPartner).where(BusinessPartner.id.in_(vendor_ids))
    )).scalars().all() if vendor_ids else []
    partner_by_id = {p.id: p for p in partners}

    out: dict[uuid.UUID, PayeeGroup] = {}
    for r in records:
        pa = pa_by_id.get(r.doc_id)
        if pa is None:
            continue
        bp = partner_by_id.get(pa.vendor_id)
        email = ""
        if bp is not None:
            # "" and NULL both mean "no email" for a vendor mirror row (EPMS
            # persists remittance_email as "" by default, not NULL) — treat
            # them identically rather than special-casing None only.
            email = (bp.remittance_email or "").strip() or (bp.contact_email or "").strip()
        g = out.get(pa.vendor_id)
        if g is None:
            g = PayeeGroup(recipient_kind=KIND_VENDOR, party_id=pa.vendor_id,
                            party_name=pa.vendor_name, email=email, currency=r.currency)
            out[pa.vendor_id] = g
        number = inv_no.get(pa.id, "")
        g.lines.append(GroupLine(vendor_inv_no=number, doc_number=pa.pa_number,
                                  payment_date=r.payment_date, amount=r.amount))
        g.total += r.amount
        g.payment_record_ids.append(r.id)

    for g in out.values():
        if not g.email:
            g.block_reasons.append(BLOCK_MISSING_EMAIL)
        # A vendor cannot reconcile a line without its own invoice number, so
        # a missing one blocks the payee rather than rendering a placeholder.
        # This applies to every vendor line, Direct PAs included.
        if any(not l.vendor_inv_no for l in g.lines):
            g.block_reasons.append(BLOCK_MISSING_INVOICE_NO)
    return list(out.values())


async def _employee_groups(db: AsyncSession,
                            records: list[PaymentRecord]) -> list[PayeeGroup]:
    if not records:
        return []
    claim_ids = [r.doc_id for r in records if r.doc_id]
    claims = (await db.execute(
        select(ExpenseClaim).where(ExpenseClaim.id.in_(claim_ids))
    )).scalars().all()
    claim_by_id = {c.id: c for c in claims}

    emp_ids = {c.employee_id for c in claims if c.employee_id}
    users = (await db.execute(
        select(User).where(User.id.in_(emp_ids))
    )).scalars().all() if emp_ids else []
    email_by_id = {u.id: (u.email or "").strip() for u in users}

    out: dict[uuid.UUID, PayeeGroup] = {}
    for r in records:
        claim = claim_by_id.get(r.doc_id)
        if claim is None or claim.employee_id is None:
            continue
        g = out.get(claim.employee_id)
        if g is None:
            g = PayeeGroup(recipient_kind=KIND_EMPLOYEE, party_id=claim.employee_id,
                            party_name=claim.employee_name,
                            email=email_by_id.get(claim.employee_id, ""),
                            currency=r.currency)
            out[claim.employee_id] = g
        # Claim number is the meaningful reference internally — employee
        # groups are exempt from the vendor-invoice-number requirement.
        g.lines.append(GroupLine(vendor_inv_no="", doc_number=claim.claim_number,
                                  payment_date=r.payment_date, amount=r.amount))
        g.total += r.amount
        g.payment_record_ids.append(r.id)

    for g in out.values():
        if not g.email:
            g.block_reasons.append(BLOCK_MISSING_EMAIL)
    return list(out.values())
