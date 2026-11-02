"""External email (design §5.5 / §13.9): the gateway protocol, an in-memory
fake, and what the outbound guard needs — the one write and the "would have
sent" summary (recipients, subject, body hash; never the body).

P6: the real gateway (`SmtpEmail`, aiosmtplib). Its account comes from the
environment — SALES_SMTP_HOST / PORT / USER / FROM / STARTTLS, the password in
SALES_SECRET_SMTP (spec X3, revised 2026-11-02: env, to keep the module
boundary; the release copies the values from epms' company_config po_smtp_*).
A send that the server did not take is NotAccepted (safe to send again); a
failure after it took the message is AcceptedThenFailed (never resent).
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ExternalEmail:
    to: tuple[str, ...]
    subject: str
    body_text: str
    body_html: str | None = None
    cc: tuple[str, ...] = ()
    reply_to: str | None = None
    attachments: tuple[tuple[str, bytes, str], ...] = field(default_factory=tuple)  # (name, data, mime)


class EmailGateway(Protocol):
    async def send_external(self, msg: ExternalEmail) -> None:
        """Raises outbox.NotAccepted when the server did not take the message
        (connection failure, refusal before DATA completed) and
        outbox.AcceptedThenFailed when it did and something later failed."""


WRITES = ("send_external",)
READS: tuple[str, ...] = ()


def guarded(switch: str) -> dict[str, str]:
    return {m: switch for m in WRITES}


def _sha256(data: bytes | str | None) -> str | None:
    if data is None:
        return None
    return hashlib.sha256(data.encode() if isinstance(data, str) else bytes(data)).hexdigest()


def summary(method: str, args: tuple, kwargs: dict) -> dict[str, Any]:  # noqa: ARG001
    msg: ExternalEmail = args[0] if args else kwargs["msg"]
    return {
        "recipients": [*msg.to, *msg.cc],
        "subject": msg.subject,
        "body_sha256": _sha256(msg.body_text),
        "body_html_sha256": _sha256(msg.body_html),
        "attachments": [{"name": n, "size": len(d), "sha256": _sha256(d)} for n, d, _ in msg.attachments],
    }


class FakeEmail:
    """`script` (optional) is called with the attempt number and may raise
    NotAccepted / AcceptedThenFailed to simulate the server's answer."""

    def __init__(self, script: Callable[[int], None] | None = None) -> None:
        self.sent: list[ExternalEmail] = []
        self.attempts = 0
        self.script = script

    async def send_external(self, msg: ExternalEmail) -> None:
        self.attempts += 1
        if self.script is not None:
            self.script(self.attempts)
        self.sent.append(msg)


class SmtpEmail:
    def __init__(self, *, host: str, port: int, username: str | None, password: Callable[[], str | None],
                 sender: str, starttls: bool = True, timeout: float = 30.0) -> None:
        self.host, self.port, self.username, self.password = host, port, username, password
        self.sender, self.starttls, self.timeout = sender, starttls, timeout

    def _message(self, msg: ExternalEmail):
        from email.message import EmailMessage
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = self.sender, ", ".join(msg.to), msg.subject
        if msg.cc:
            m["Cc"] = ", ".join(msg.cc)
        if msg.reply_to:
            m["Reply-To"] = msg.reply_to
        m.set_content(msg.body_text)
        if msg.body_html:
            m.add_alternative(msg.body_html, subtype="html")
        for name, data, mime in msg.attachments:
            main, _, sub = mime.partition("/")
            m.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=name)
        return m

    async def send_external(self, msg: ExternalEmail) -> None:
        import aiosmtplib

        from app.platform.outbox import AcceptedThenFailed, NotAccepted
        if not self.host:
            raise NotAccepted("no SMTP server configured (SALES_SMTP_HOST)")
        try:
            await aiosmtplib.send(
                self._message(msg), hostname=self.host, port=self.port, username=self.username or None,
                password=(self.password() or None) if self.username else None, start_tls=self.starttls,
                timeout=self.timeout, recipients=[*msg.to, *msg.cc])
        except (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPConnectTimeoutError, aiosmtplib.SMTPAuthenticationError,
                aiosmtplib.SMTPRecipientsRefused, aiosmtplib.SMTPSenderRefused, aiosmtplib.SMTPDataError,
                aiosmtplib.SMTPHeloError, aiosmtplib.SMTPNotSupported, OSError) as e:
            raise NotAccepted(f"the mail server did not take the message: {type(e).__name__}") from e
        except aiosmtplib.SMTPException as e:
            raise AcceptedThenFailed(f"the mail server failed after the message: {type(e).__name__}") from e
