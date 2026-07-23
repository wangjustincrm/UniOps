"""Send remittance advice and record the outcome.

Never called inside the payment transaction: the caller commits the payment
first, then sends. One payee's SMTP failure is isolated and logged; the rest
still go out. Blocked payees are refused here as well as in the UI — a client
that posts one anyway gets `skipped`, never `sent`, and never an actual email.
"""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.remittance import PayeeGroup
from app.models.remittance import FAILED, SENT, RemittanceNotification
from app.services.email import send_email
from app.services.remittance_config import RemittanceSettings
from app.services.remittance_template import render

logger = logging.getLogger(__name__)


async def _upsert(db: AsyncSession, *, scope_kind: str, scope_id: uuid.UUID,
                   group: PayeeGroup, status: str, error: str | None,
                   actor_id: uuid.UUID) -> None:
    # Unique key is (scope_kind, scope_id, recipient_kind, party_id) — a
    # resend updates the existing row and bumps attempts rather than
    # inserting a duplicate.
    row = (await db.execute(
        select(RemittanceNotification).where(
            RemittanceNotification.scope_kind == scope_kind,
            RemittanceNotification.scope_id == scope_id,
            RemittanceNotification.recipient_kind == group.recipient_kind,
            RemittanceNotification.party_id == group.party_id,
        )
    )).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = RemittanceNotification(
            scope_kind=scope_kind, scope_id=scope_id,
            recipient_kind=group.recipient_kind, party_id=group.party_id,
            party_name=group.party_name, email=group.email,
            payment_record_ids=[str(i) for i in group.payment_record_ids],
            amount=group.total, currency=group.currency,
            status=status, error=error, attempts=1,
            sent_at=now if status == SENT else None, created_by=actor_id,
        )
        db.add(row)
    else:
        row.party_name = group.party_name
        row.email = group.email
        row.payment_record_ids = [str(i) for i in group.payment_record_ids]
        row.amount = group.total
        row.currency = group.currency
        row.status = status
        row.error = error
        row.attempts = (row.attempts or 0) + 1
        if status == SENT:
            row.sent_at = now
    await db.flush()


async def send_groups(db: AsyncSession, *, scope_kind: str, scope_id: uuid.UUID,
                       groups: list[PayeeGroup], reference: str,
                       payment_method: str, company_name: str,
                       sender: RemittanceSettings,
                       actor_id: uuid.UUID) -> list[dict]:
    """Send one email per payee group and record the outcome.

    Blocked or emailless groups are refused server-side and never reach
    send_email or the log — this is the enforcement point, not just the UI's
    greyed-out checkbox. One payee's SMTP failure is caught, logged against
    that payee, and does not stop the rest of the loop.
    """
    results: list[dict] = []
    for g in groups:
        base = {"recipient_kind": g.recipient_kind, "party_id": str(g.party_id),
                "party_name": g.party_name}
        if g.block_reasons or not g.email:
            results.append({**base, "status": "skipped",
                             "error": ", ".join(g.block_reasons) or "missing_email"})
            continue
        subject, html = render(g, company_name=company_name, reference=reference,
                                payment_method=payment_method)
        try:
            await send_email(
                g.email, subject, html, cc=sender.cc_email,
                smtp_host=sender.smtp_host, smtp_port=sender.smtp_port,
                smtp_user=sender.smtp_user, smtp_password=sender.smtp_password,
                smtp_use_tls=sender.smtp_use_tls,
                smtp_from=(f"{sender.from_name} <{sender.from_email}>"
                           if sender.from_name else sender.from_email),
            )
        except Exception as exc:  # noqa: BLE001 — isolate one payee's failure
            logger.error("Remittance send failed for %s: %s", g.email, exc)
            await _upsert(db, scope_kind=scope_kind, scope_id=scope_id, group=g,
                          status=FAILED, error=str(exc)[:500], actor_id=actor_id)
            results.append({**base, "status": "failed", "error": str(exc)[:500]})
            continue
        await _upsert(db, scope_kind=scope_kind, scope_id=scope_id, group=g,
                      status=SENT, error=None, actor_id=actor_id)
        results.append({**base, "status": "sent", "error": None})
    return results
