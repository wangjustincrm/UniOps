"""Send remittance advice and record the outcome.

Never called inside the payment transaction: the caller commits the payment
first, then sends. One payee's failure — whether rendering the email or
sending it — is isolated and logged; the rest still go out. Blocked payees
are refused here as well as in the UI — a client that posts one anyway gets
`skipped`, never `sent`, and never an actual email.

Sending an email is an irreversible act. Until the log row for that send is
committed, the record of it only exists in this session's uncommitted
transaction — if anything later (the next payee, or the caller) raises before
that transaction is committed, the email already went out but the log looks
like it never happened, and an operator who sees "not sent" will press Send
again and double-send the vendor. So this module commits the log row right
after each payee's send attempt, not once in a trailing commit at the end of
the loop. This is safe here BY DESIGN ONLY because remittance sending
deliberately runs *after* the payment transaction has already been committed
by the caller — there is no unrelated payment work sitting uncommitted in
this session that a per-payee `db.commit()` could accidentally flush early.
Do not "optimize" this back into a single trailing commit.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.remittance import PayeeGroup
from app.models.remittance import FAILED, SENT, RemittanceNotification
from app.services.email import send_email
from app.services.remittance_config import RemittanceSettings
from app.services.remittance_template import render


async def _upsert(db: AsyncSession, *, scope_kind: str, scope_id: uuid.UUID,
                   group: PayeeGroup, status: str, error: str | None,
                   actor_id: uuid.UUID) -> None:
    """Insert or update the log row for one payee, atomically.

    Unique key is (scope_kind, scope_id, recipient_kind, party_id) — a
    resend updates the existing row and bumps attempts rather than
    inserting a duplicate. This uses PostgreSQL's atomic `INSERT ...
    ON CONFLICT DO UPDATE` rather than a SELECT followed by an insert/update:
    two overlapping sends for the same payee (a double-clicked Send button,
    or a resend fired while the first send is still in flight) can both miss
    the SELECT and both attempt an insert — with SELECT-then-write the second
    would raise IntegrityError *after* its email had already gone out. The
    atomic upsert instead lands the second caller cleanly on the update path,
    still correctly incrementing `attempts` from whatever the row's current
    value is.
    """
    now = datetime.now(timezone.utc)
    stmt = pg_insert(RemittanceNotification).values(
        scope_kind=scope_kind, scope_id=scope_id,
        recipient_kind=group.recipient_kind, party_id=group.party_id,
        party_name=group.party_name, email=group.email,
        payment_record_ids=[str(i) for i in group.payment_record_ids],
        amount=group.total, currency=group.currency,
        status=status, error=error, attempts=1,
        sent_at=now if status == SENT else None, created_by=actor_id,
    )
    update_cols = {
        "party_name": stmt.excluded.party_name,
        "email": stmt.excluded.email,
        "payment_record_ids": stmt.excluded.payment_record_ids,
        "amount": stmt.excluded.amount,
        "currency": stmt.excluded.currency,
        "status": stmt.excluded.status,
        "error": stmt.excluded.error,
        "attempts": RemittanceNotification.attempts + 1,
    }
    if status == SENT:
        update_cols["sent_at"] = now
    stmt = stmt.on_conflict_do_update(
        constraint="uq_remittance_scope_party", set_=update_cols,
    )
    await db.execute(stmt)
    await db.flush()


async def send_groups(db: AsyncSession, *, scope_kind: str, scope_id: uuid.UUID,
                       groups: list[PayeeGroup], reference: str,
                       payment_method: str, company_name: str,
                       sender: RemittanceSettings,
                       actor_id: uuid.UUID) -> list[dict]:
    """Send one email per payee group and record the outcome.

    Blocked or emailless groups are refused server-side and never reach
    render, send_email, or the log — this is the enforcement point, not just
    the UI's greyed-out checkbox. One payee's failure — in `render()` or in
    `send_email()` — is caught, logged against that payee, committed, and
    does not stop the rest of the loop.
    """
    results: list[dict] = []
    for g in groups:
        base = {"recipient_kind": g.recipient_kind, "party_id": str(g.party_id),
                "party_name": g.party_name}
        if g.block_reasons or not g.email:
            # The real group builder always appends BLOCK_MISSING_EMAIL
            # whenever email is blank, so block_reasons is never empty here.
            results.append({**base, "status": "skipped",
                             "error": ", ".join(g.block_reasons)})
            continue
        try:
            subject, html = render(g, company_name=company_name, reference=reference,
                                    payment_method=payment_method)
            await send_email(
                g.email, subject, html, cc=sender.cc_email,
                smtp_host=sender.smtp_host, smtp_port=sender.smtp_port,
                smtp_user=sender.smtp_user, smtp_password=sender.smtp_password,
                smtp_use_tls=sender.smtp_use_tls,
                smtp_from=(f"{sender.from_name} <{sender.from_email}>"
                           if sender.from_name else sender.from_email),
            )
        except Exception as exc:  # noqa: BLE001 — isolate one payee's failure
            # send_email() already logs and re-raises SMTP failures; a
            # render() failure never reaches send_email. Either way, the
            # RemittanceNotification row below is the durable record of the
            # failure — no second log line for what send_email already logged.
            await _upsert(db, scope_kind=scope_kind, scope_id=scope_id, group=g,
                          status=FAILED, error=str(exc)[:500], actor_id=actor_id)
            # Commit per payee, right here — see module docstring. A send
            # that actually happened (or actually failed) must be durable
            # before moving on to the next payee.
            await db.commit()
            results.append({**base, "status": "failed", "error": str(exc)[:500]})
            continue
        await _upsert(db, scope_kind=scope_kind, scope_id=scope_id, group=g,
                      status=SENT, error=None, actor_id=actor_id)
        # Commit per payee, right here — see module docstring.
        await db.commit()
        results.append({**base, "status": "sent", "error": None})
    return results
