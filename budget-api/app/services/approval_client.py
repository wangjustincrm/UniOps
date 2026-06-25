"""HTTP client to delegate plan actions to approval-api.

approval-api owns the workflow state machine (configurable via Portal Admin).
budget-api only invokes /approvals/{doc_type}/{doc_id}/action and lets
approval-api update budget_plans.status and approval_step_idx via its
read-write mirror model.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


def _auth_headers(bearer_token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}


async def execute_plan_action(
    *,
    bearer_token: str | None,
    plan_id: uuid.UUID,
    action: str,
    comment: str | None = None,
) -> dict[str, Any]:
    """POST /approvals/budget_plan/{plan_id}/action — returns ActionResult JSON."""
    url = f"{settings.APPROVAL_API_URL}/approvals/budget_plan/{plan_id}/action"
    body = {"action": action}
    if comment is not None:
        body["comment"] = comment
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(url, json=body, headers=_auth_headers(bearer_token))
        if r.status_code >= 400:
            logger.warning("approval-api action %s on plan %s returned %d: %s",
                           action, plan_id, r.status_code, r.text)
            r.raise_for_status()
        return r.json()
