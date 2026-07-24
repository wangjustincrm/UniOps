"""Async email sending via aiosmtplib.

Ported from epms-api/app/services/email.py (2026-07-22) — the send primitive
only, no MFA or PO template code. Keep the TLS mode decision below in sync
with that file; getting it wrong yields [SSL: WRONG_VERSION_NUMBER] at
runtime and nothing at test time.
"""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, html: str, *, cc: str | None = None,
                     smtp_host: str, smtp_port: int, smtp_user: str | None,
                     smtp_password: str | None, smtp_use_tls: bool,
                     smtp_from: str) -> None:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg.attach(MIMEText(html, "html"))

    # use_tls   → handshake on connect (implicit TLS / SMTPS, port 465)
    # start_tls → connect plaintext, upgrade via STARTTLS (submission, 587)
    implicit_tls = bool(smtp_use_tls) and smtp_port == 465
    start_tls = bool(smtp_use_tls) and smtp_port != 465

    try:
        await aiosmtplib.send(
            msg, hostname=smtp_host, port=smtp_port,
            username=smtp_user or None, password=smtp_password or None,
            use_tls=implicit_tls, start_tls=start_tls,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send remittance email to %s: %s", to, exc)
        raise
