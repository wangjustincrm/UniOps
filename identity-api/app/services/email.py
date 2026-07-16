"""Async email sending via aiosmtplib (copied from epms-api, MFA subset).

⚠️ This is a trimmed copy of epms-api/app/services/email.py (no attachments/cc
here — MFA only needs plain HTML). The TLS-mode logic below MUST stay in sync
with the epms-api original: if that file's TLS handling changes, mirror the
change here too. (2026-07 incident: this copy was missing epms's start_tls
fix, so port-587 STARTTLS servers got an implicit-TLS handshake and failed
with [SSL: WRONG_VERSION_NUMBER].)
"""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_email(
    to: str,
    subject: str,
    html: str,
    *,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    smtp_use_tls: bool | None = None,
    smtp_from: str | None = None,
) -> None:
    host = smtp_host or settings.SMTP_HOST
    port = smtp_port or settings.SMTP_PORT
    user = smtp_user if smtp_user is not None else settings.SMTP_USER
    password = smtp_password if smtp_password is not None else settings.SMTP_PASSWORD
    use_tls = smtp_use_tls if smtp_use_tls is not None else settings.SMTP_USE_TLS
    from_addr = smtp_from or settings.SMTP_FROM

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))

    # aiosmtplib distinguishes two TLS modes that the single "Use TLS/STARTTLS"
    # toggle conflates:
    #   use_tls   → handshake immediately on connect (implicit TLS / SMTPS, port 465)
    #   start_tls → connect plaintext, then upgrade via STARTTLS (submission, port 587)
    # Forcing use_tls on a 587 STARTTLS server yields [SSL: WRONG_VERSION_NUMBER].
    implicit_tls = bool(use_tls) and port == 465
    start_tls = bool(use_tls) and port != 465

    try:
        await aiosmtplib.send(
            msg, hostname=host, port=port,
            username=user or None, password=password or None,
            use_tls=implicit_tls, start_tls=start_tls,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send email to %s: %s", to, exc)
        raise


async def send_mfa_otp(to: str, otp: str, **smtp_overrides) -> None:
    html = (
        "<p>Your UniOps verification code is:</p>"
        f"<h2 style='letter-spacing:4px'>{otp}</h2>"
        "<p>This code expires in 10 minutes. If you did not request it, "
        "please contact your administrator.</p>"
    )
    await send_email(to, "UniOps verification code", html, **smtp_overrides)
