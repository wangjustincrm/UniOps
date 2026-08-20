"""HTTP client — Identity → Approval Engine.

Identity owns who holds which role; approval-api owns the in-flight approval
tasks that were routed by those roles. Approve tasks for department-scoped roles
are PINNED to a specific person when created (approval-api engine's
_USER_SPECIFIC_ROLES), so a role change here must tell the engine to re-resolve
them — otherwise they stay with the previous holder (prod incident 2026-08-18).
"""
import logging

import httpx

from app.core.config import settings

_log = logging.getLogger(__name__)


async def resync_inflight(bearer_token: str) -> dict:
    """Call POST /routing/resync-inflight on the Approval Engine, forwarding the
    caller's own token (both ends are system_admin-gated).

    Raises RuntimeError on unreachable / error — the caller has already committed
    the role change and reports this as a warning rather than failing the edit.
    """
    url = f"{settings.APPROVAL_ENGINE_URL}/routing/resync-inflight"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {bearer_token}"})
    if not resp.is_success:
        raise RuntimeError(f"resync-inflight {resp.status_code}: {resp.text[:200]}")
    _log.info("outbound | service=approval-api | resync-inflight | status=%d", resp.status_code)
    return resp.json()
