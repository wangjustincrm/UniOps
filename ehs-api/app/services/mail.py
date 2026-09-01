"""Sending Safety's email, and keeping a copy of everything it sent.

Two things happen on every send and the second is the point. HSE asked that
"all emails sent and received by the system are automatically filed against the
corresponding record, forming an audit trail" — so a message is not fired off
and forgotten: it is written into `ehs_communications` attached to the incident,
action or document it is about. Three years later, "were they told?" has an
answer.

Sending is two steps on purpose. `queue()` writes the archive row inside the
caller's own transaction, so the record of "we told them" commits or rolls back
with the thing it is about — an incident that failed to save cannot leave
behind a note saying it was circulated. `deliver()` then does the network call
in the background with its own session, so a slow or dead mail server holds up
nobody's request.

A message that failed to leave is a row with no `sent_at`. That is the honest
record, and it is the case somebody asks about later; stamping the row only on
success would leave no trace of the attempt at all.

SMTP credentials come from `company_config`, the same row epms-api uses. Safety
sends its own mail rather than routing through epms-api — the messages are
Safety's, and an HTTP hop to another service to send them would add a failure
mode and return nothing.

Nothing here raises. A statutory deadline must not fail to escalate because a
mail server was down.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import background
from app.core.config import settings
from app.models.communication import Communication
from app.models.mirrors import CompanyConfig, User

logger = logging.getLogger(__name__)

# Recipients whose channel is one of these are skipped. `notification_channel`
# is NOT NULL with a server default in production, so it is read as the
# person's own choice and not fallen back over.
OPTED_OUT = {"none", "teams_only"}


async def _config(db: AsyncSession) -> CompanyConfig | None:
    return (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()


def _smtp(cfg: CompanyConfig | None) -> dict:
    """Company settings first, environment as the fallback."""
    return {
        "host": (cfg.smtp_host if cfg else None) or settings.SMTP_HOST,
        "port": (cfg.smtp_port if cfg else None) or settings.SMTP_PORT,
        "user": (cfg.smtp_user if cfg else None) or settings.SMTP_USER,
        "password": (cfg.smtp_password if cfg else None) or settings.SMTP_PASSWORD,
        "use_tls": (cfg.smtp_use_tls if cfg and cfg.smtp_use_tls is not None
                    else settings.SMTP_USE_TLS),
        "from_addr": (cfg.smtp_from if cfg else None) or settings.SMTP_FROM,
    }


def render(body: str, *, title: str, link: str | None = None,
           link_label: str = "Open it in Safety") -> str:
    """The one HTML shell every Safety email uses.

    Deliberately plain: these are read on a phone in a corridor, often by
    somebody who has thirty seconds. One heading, the text, one link.
    """
    button = ""
    if link:
        button = (
            f'<p style="margin:20px 0 0"><a href="{link}" '
            'style="background:#0f766e;color:#fff;padding:10px 18px;border-radius:6px;'
            'text-decoration:none;font-weight:600;display:inline-block">'
            f'{link_label}</a></p>'
        )
    safe_body = body.replace("\n", "<br>")
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'max-width:560px;color:#1f2937;line-height:1.5">'
        f'<h2 style="margin:0 0 12px;font-size:17px;color:#0f172a">{title}</h2>'
        f'<div style="font-size:14px">{safe_body}</div>'
        f'{button}'
        '<p style="margin:24px 0 0;font-size:12px;color:#6b7280">'
        'Sent by UniOps Safety. Replies to this address are not monitored yet.'
        '</p></div>'
    )


def module_link(path: str) -> str:
    """An absolute URL into the Safety front end."""
    return f"{settings.SAFETY_WEB_URL.rstrip('/')}{path}"


async def recipients(
    db: AsyncSession, user_ids: list[uuid.UUID | None]
) -> list[User]:
    """Active people with an address who have not opted out of email.

    Silently dropping an opted-out recipient is correct here: this is the
    module's second channel, not its only one. Everything that needs doing has
    a task in the inbox as well.
    """
    ids = [u for u in user_ids if u]
    if not ids:
        return []
    rows = (await db.execute(
        select(User).where(User.id.in_(ids), User.is_active.is_(True)))).scalars().all()
    return [u for u in rows
            if u.email and (u.notification_channel or "email_only") not in OPTED_OUT]


async def queue(
    db: AsyncSession,
    *,
    to: list[str],
    subject: str,
    body: str,
    doc_type: str | None = None,
    doc_id: uuid.UUID | None = None,
    template_key: str | None = None,
    link: str | None = None,
    link_label: str = "Open it in Safety",
    cc: list[str] | None = None,
) -> Communication | None:
    """File the message against its record and schedule the send.

    Runs in the caller's transaction. Returns the archive row, or None when
    there was nobody to send to.
    """
    to = [a for a in dict.fromkeys(a.strip() for a in to if a and a.strip())]
    if not to:
        return None

    from_addr = _smtp(await _config(db))["from_addr"] or ""
    html = render(body, title=subject, link=link, link_label=link_label)

    record = Communication(
        id=uuid.uuid4(),
        doc_type=doc_type, doc_id=doc_id,
        direction="out",
        from_addr=from_addr,
        to_addrs=list(to), cc_addrs=list(cc or []),
        subject=subject, body_text=body, body_html=html,
        # Set now and sent as the header, so an inbound reply's In-Reply-To can
        # be matched back to this row when the inbound channel is built.
        message_id=make_msgid(domain="safety.uniops"),
        template_key=template_key,
    )
    db.add(record)
    await db.flush()

    # Tracked rather than a bare create_task: a discarded task takes its
    # database session down with it, still holding a transaction.
    background.spawn(deliver(record.id), name=f"ehs-mail-{record.id}")
    return record


async def deliver(communication_id: uuid.UUID) -> bool:
    """Transmit one queued message. Own session, own errors, never raises.

    Returns whether it left. Sending twice is refused by the `sent_at` check —
    the caller's transaction may be retried, and a duplicate notice about an
    injury is worse than a late one.
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        record = await db.get(Communication, communication_id)
        if record is None or record.sent_at is not None:
            return False

        smtp = _smtp(await _config(db))
        if not smtp["host"]:
            # The row stays, unsent. "We meant to tell them and could not" is a
            # fact worth keeping, and it is the one asked about afterwards.
            logger.warning("Safety mail not sent (%s): no SMTP host configured",
                           record.subject)
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = record.subject or ""
        msg["From"] = smtp["from_addr"] or record.from_addr
        msg["To"] = ", ".join(record.to_addrs or [])
        if record.cc_addrs:
            msg["Cc"] = ", ".join(record.cc_addrs)
        msg["Message-ID"] = record.message_id or make_msgid(domain="safety.uniops")
        msg.attach(MIMEText(record.body_text or "", "plain"))
        msg.attach(MIMEText(record.body_html or "", "html"))

        try:
            import aiosmtplib

            port = int(smtp["port"] or 587)
            # aiosmtplib distinguishes two TLS modes the single "use TLS"
            # toggle conflates. Forcing implicit TLS on a 587 submission server
            # yields [SSL: WRONG_VERSION_NUMBER] — this codebase has been there.
            implicit = bool(smtp["use_tls"]) and port == 465
            await aiosmtplib.send(
                msg,
                hostname=smtp["host"],
                port=port,
                username=smtp["user"] or None,
                password=smtp["password"] or None,
                use_tls=implicit,
                start_tls=bool(smtp["use_tls"]) and not implicit,
            )
        except Exception:
            logger.exception("Safety mail failed: %s -> %s",
                             record.subject, record.to_addrs)
            await db.commit()
            return False

        record.sent_at = datetime.now(timezone.utc)
        await db.commit()
        return True


async def history(
    db: AsyncSession, *, doc_type: str, doc_id: uuid.UUID
) -> list[Communication]:
    """Everything sent about one record, oldest first."""
    return list((await db.execute(
        select(Communication)
        .where(Communication.doc_type == doc_type, Communication.doc_id == doc_id,
               Communication.is_auto_reply.is_(False))
        .order_by(Communication.created_at)
    )).scalars())
