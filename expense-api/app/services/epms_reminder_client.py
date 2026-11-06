"""HTTP client — expense-api → epms-api manual approval reminder.

The notification machinery (SMTP, delegation stand-ins, role pools, shared
mailboxes, the 24h cooldown in notification_logs) exists only in epms-api, so
the Approval Status card's "Send reminder" is forwarded there rather than
re-implemented here. See epms-api/app/api/v1/expense_reminders.py.
"""
import logging
import uuid

import httpx

from app.core.config import settings

_log = logging.getLogger(__name__)


class ReminderRefused(Exception):
    """epms-api answered with a user-facing refusal (409 / 429 / 404 / 422)."""

    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


async def send_expense_reminder(
    claim_id: uuid.UUID, claim_type: str, bearer_token: str,
) -> dict:
    url = f"{settings.epms_api_url}/expense-claims/{claim_id}/remind"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={"claim_type": claim_type},
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
    except httpx.HTTPError as exc:
        _log.error("outbound | service=epms-api | remind claim=%s | error=%s", claim_id, exc)
        raise RuntimeError("The notification service is unreachable, so no reminder was sent.")

    _log.info("outbound | service=epms-api | remind claim=%s | status=%d", claim_id, resp.status_code)
    if resp.status_code in (404, 409, 422, 429):
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = None
        raise ReminderRefused(resp.status_code, detail or "Could not send the reminder.")
    if not resp.is_success:
        _log.error("outbound | service=epms-api | remind claim=%s | body=%s", claim_id, resp.text[:200])
        raise RuntimeError(f"Could not send the reminder (notification service error {resp.status_code}).")
    return resp.json()
