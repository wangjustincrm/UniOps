"""Async email sending via aiosmtplib."""
import logging
from email.mime.application import MIMEApplication
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
    cc: str | None = None,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    smtp_use_tls: bool | None = None,
    smtp_from: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    """Send an HTML email with optional file attachments.

    attachments: list of (filename, bytes, content_type) tuples.
    """
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
    if cc:
        msg["Cc"] = cc
    msg.attach(MIMEText(html, "html"))

    for filename, data, content_type in (attachments or []):
        part = MIMEApplication(data, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        part["Content-Type"] = f'{content_type}; name="{filename}"'
        msg.attach(part)

    try:
        await aiosmtplib.send(
            msg,
            hostname=host,
            port=port,
            username=user or None,
            password=password or None,
            use_tls=use_tls,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send email to %s: %s", to, exc)
        raise


async def send_mfa_otp(
    to: str,
    code: str,
    *,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    smtp_use_tls: bool | None = None,
    smtp_from: str | None = None,
) -> None:
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto">
      <h2 style="color:#085E5E">EPMS — Verification Code</h2>
      <p>Use the code below to complete your sign-in. It expires in <strong>10 minutes</strong>.</p>
      <div style="font-size:36px;font-weight:bold;letter-spacing:12px;
                  background:#f4f4f4;border-radius:8px;padding:20px 32px;
                  text-align:center;margin:24px 0">{code}</div>
      <p style="color:#999;font-size:12px">
        If you did not attempt to sign in, please ignore this email and contact your administrator.
      </p>
    </div>
    """
    await send_email(
        to, "EPMS — Your sign-in verification code", html,
        smtp_host=smtp_host, smtp_port=smtp_port, smtp_user=smtp_user,
        smtp_password=smtp_password, smtp_use_tls=smtp_use_tls, smtp_from=smtp_from,
    )
