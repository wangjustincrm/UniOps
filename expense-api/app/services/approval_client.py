"""HTTP client — expense-api → Approval Engine delegation."""
import logging
import time

import httpx
from app.core.config import settings

_log = logging.getLogger(__name__)


async def delegate_action(
    doc_type: str,
    doc_id: str,
    action: str,
    comment: str | None,
    bearer_token: str,
) -> dict:
    url = f"{settings.approval_engine_url}/approvals/{doc_type}/{doc_id}/action"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={"action": action, "comment": comment},
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
    except httpx.ConnectError:
        dur = int((time.monotonic() - t0) * 1000)
        _log.error(
            "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=%s | status=CONN_ERROR | dur=%dms"
            " — Is approval-api running on %s?",
            doc_type, doc_id, action, dur, settings.approval_engine_url,
        )
        raise RuntimeError(
            f"Approval Engine unreachable at {settings.approval_engine_url}. "
            "Is approval-api running? Run: ./check-health.sh"
        )

    dur = int((time.monotonic() - t0) * 1000)
    _log.info(
        "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=%s | status=%d | dur=%dms",
        doc_type, doc_id, action, resp.status_code, dur,
    )

    if resp.status_code in (409, 422):
        detail = resp.json().get("detail", "Conflict" if resp.status_code == 409 else "Validation error")
        raise ValueError(detail)
    if resp.status_code == 404:
        raise LookupError(resp.json().get("detail", "Not found"))
    if not resp.is_success:
        _log.error(
            "outbound | service=approval-api | doc_type=%s | doc_id=%s | status=%d | body=%s",
            doc_type, doc_id, resp.status_code, resp.text[:200],
        )
        raise RuntimeError(f"Approval Engine error {resp.status_code}: {resp.text[:200]}")

    return resp.json()
