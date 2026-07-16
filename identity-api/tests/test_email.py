"""TLS-mode regression tests for the identity email service.

2026-07 production incident: company MFA rollout made OTP delivery mandatory,
and the identity SMTP client failed with `[SSL: WRONG_VERSION_NUMBER]` on the
company's port-587 STARTTLS relay. Root cause: `send_email` only forwarded
`use_tls` to aiosmtplib, so `use_tls=True` on port 587 forced an implicit-TLS
handshake on a plaintext/STARTTLS port. epms-api already carries the fix
(distinguish `use_tls` vs `start_tls` by port); these tests pin identity's
copy to the same behavior so the two can't drift again.
"""
from unittest.mock import AsyncMock, patch

from app.services.email import send_email


async def test_port_587_with_use_tls_upgrades_via_starttls():
    """The incident scenario: port 587 + use_tls=True must NOT do implicit TLS."""
    with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await send_email(
            "it@canadaroyalmilk.com", "subject", "<p>body</p>",
            smtp_host="mail.canadaroyalmilk.com", smtp_port=587,
            smtp_use_tls=True,
        )
    _, kwargs = mock_send.call_args
    assert kwargs["start_tls"] is True
    assert kwargs["use_tls"] is False


async def test_port_465_with_use_tls_does_implicit_tls():
    with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await send_email(
            "it@canadaroyalmilk.com", "subject", "<p>body</p>",
            smtp_host="mail.canadaroyalmilk.com", smtp_port=465,
            smtp_use_tls=True,
        )
    _, kwargs = mock_send.call_args
    assert kwargs["use_tls"] is True
    assert kwargs["start_tls"] is False


async def test_use_tls_false_stays_unencrypted():
    """Sanity check: explicitly disabling TLS must not silently upgrade to STARTTLS."""
    with patch("app.services.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await send_email(
            "it@canadaroyalmilk.com", "subject", "<p>body</p>",
            smtp_host="mail.canadaroyalmilk.com", smtp_port=587,
            smtp_use_tls=False,
        )
    _, kwargs = mock_send.call_args
    assert kwargs["use_tls"] is False
    assert kwargs["start_tls"] is False
