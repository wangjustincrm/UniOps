"""HTTP client to read NC posted actuals from finance-api.

`actual_spent` on a balance is NC's posted figure, not this service's own
`budget_ledger`. The ledger was designed to carry it (commit / release /
actualize / book_expense) but the purchase chain was never wired to write to
it: in production it holds one 2026-07-07 opening import and nothing else, so
reading it back reports a year-old opening balance as the year's spend. The
Budget Dashboard already reads NC directly; this client makes `/balance` ask
the same question of the same service, so the PR over-budget test and the
dashboard cannot disagree.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal, InvalidOperation

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


def _auth_headers(bearer_token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}


async def nc_actual_for_budget_check(
    *,
    bearer_token: str | None,
    cost_center_id: uuid.UUID,
    account_id: uuid.UUID,
    fiscal_year: int,
) -> Decimal | None:
    """NC posted actual for one (cost center × account × year).

    Returns **None** — never Decimal("0") — when finance-api is unreachable,
    refuses, or answers something unparseable. The distinction is the whole
    point: a zero here would be read as "nothing has been spent", `available`
    would come back as the full annual budget, and every PR would pass the
    over-budget test. Callers must treat None as "unknown" and fall back, not
    as an amount.
    """
    url = f"{settings.FINANCE_API_URL}/finance/v1/gl/nc-actual-for-budget-check"
    params = {
        "fiscal_year": fiscal_year,
        "cost_center_id": str(cost_center_id),
        "account_id": str(account_id),
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params=params, headers=_auth_headers(bearer_token))
        if r.status_code >= 400:
            logger.warning(
                "finance-api nc-actual-for-budget-check cc=%s account=%s fy=%s "
                "returned %d: %s", cost_center_id, account_id, fiscal_year,
                r.status_code, r.text[:300],
            )
            return None
        return Decimal(str(r.json()["actual"]))
    except (httpx.HTTPError, KeyError, TypeError, ValueError, InvalidOperation) as e:
        logger.warning(
            "finance-api nc-actual-for-budget-check cc=%s account=%s fy=%s failed: %s",
            cost_center_id, account_id, fiscal_year, e,
        )
        return None
