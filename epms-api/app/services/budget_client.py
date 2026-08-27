"""HTTP client for the budget-api microservice.

Used by epms-api in place of the deleted in-process budget module
(crud/budget.py, models/budget.py, api/v1/budget.py).

Fail-open: on network / 5xx errors the balance lookup returns "infinite available"
so PR submission isn't blocked by budget-api downtime. The dashboard helpers
return empty summaries.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


def _auth_headers(bearer_token: str | None) -> dict[str, str]:
    if bearer_token:
        return {"Authorization": f"Bearer {bearer_token}"}
    return {}


async def get_balance(
    bearer_token: str | None,
    cost_center_id: uuid.UUID,
    fiscal_year: int,
    account_id: uuid.UUID | None = None,
    account_code: str | None = None,
) -> dict[str, Any] | None:
    """Returns {annual_budget, committed, actual_spent, available} or None on failure."""
    params: dict[str, Any] = {
        "cost_center_id": str(cost_center_id),
        "fiscal_year": fiscal_year,
    }
    if account_id is not None:
        params["account_id"] = str(account_id)
    if account_code is not None:
        params["account_code"] = account_code
    url = f"{settings.BUDGET_API_URL}/api/v1/balance"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params=params, headers=_auth_headers(bearer_token))
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        logger.warning("budget-api /balance unreachable: %s — failing open", e)
        return None


async def get_account_name(bearer_token: str | None, code: str | None) -> str | None:
    """The budget account's display name for a given code, or None.

    budget-api exposes no by-code lookup, so this pulls the catalog (168 rows
    today) and matches client-side -- the same thing the PR Detail page does
    with its cached catalog query.

    Fail-open like the rest of this module: a document must still render when
    budget-api is unreachable, just with the bare code as before.
    """
    if not code:
        return None
    url = f"{settings.BUDGET_API_URL}/api/v1/accounts"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=_auth_headers(bearer_token))
            r.raise_for_status()
            for acct in r.json():
                if acct.get("code") == code:
                    return acct.get("name") or None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("budget-api /accounts unreachable: %s — rendering the code alone", e)
    return None


async def compute_over_budget(
    bearer_token: str | None,
    cost_center_id: uuid.UUID | None,
    fiscal_year: int,
    budget_code: str | None,
    pr_amount: Decimal,
) -> bool:
    """Returns True if pr_amount would push the account into negative available.

    Fail-open: returns False if budget-api is unreachable or account not found.
    """
    if not budget_code or cost_center_id is None or pr_amount <= 0:
        return False
    data = await get_balance(
        bearer_token, cost_center_id, fiscal_year, account_code=budget_code,
    )
    if data is None:
        return False
    available = Decimal(str(data.get("available", 0)))
    return pr_amount > available


async def get_actuals_summary(
    bearer_token: str | None,
    fiscal_year: int,
    cost_center_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """Returns the budget-api /actuals/summary response, or None on failure."""
    params: dict[str, Any] = {"fiscal_year": fiscal_year}
    if cost_center_id is not None:
        params["cost_center_id"] = str(cost_center_id)
    url = f"{settings.BUDGET_API_URL}/api/v1/actuals/summary"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params=params, headers=_auth_headers(bearer_token))
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        logger.warning("budget-api /actuals/summary unreachable: %s", e)
        return None
