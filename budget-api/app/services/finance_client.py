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


async def nc_actuals_by_account(
    *,
    bearer_token: str | None,
    fiscal_year: int,
    cost_center_id: uuid.UUID | None,
) -> dict[str, Decimal] | None:
    """NC posted actual per budget account, year total — every account at once.

    The per-account figures behind `/actuals/summary`, which is what the OA
    expense-claim account picker shows as each account's remaining budget. It
    reads `/gl/nc-actuals-monthly`, the Budget Dashboard's own NC line, with
    the caller's token: that endpoint clamps to the caller's department by the
    same budget-scope rule this service applies to the plan side, so the
    budget and the spend it is set against cover the same cost centres.

    Keyed by `str(account_id)`. An account missing from the map is a real
    zero (NC has posted nothing against it) and callers should take it as one.
    Returns **None** — never an empty map — when finance-api can't be had; see
    `nc_actual_for_budget_check` for why "unknown" must not read as "nothing".
    """
    url = f"{settings.FINANCE_API_URL}/finance/v1/gl/nc-actuals-monthly"
    params: dict[str, object] = {"fiscal_year": fiscal_year}
    if cost_center_id is not None:
        params["cost_center_id"] = str(cost_center_id)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params=params, headers=_auth_headers(bearer_token))
        if r.status_code >= 400:
            logger.warning(
                "finance-api nc-actuals-monthly cc=%s fy=%s returned %d: %s",
                cost_center_id, fiscal_year, r.status_code, r.text[:300],
            )
            return None
        accounts = r.json()["accounts"]
        return {
            aid: sum((Decimal(str(v)) for v in months.values()), Decimal("0"))
            for aid, months in accounts.items()
        }
    except (httpx.HTTPError, KeyError, TypeError, ValueError, AttributeError,
            InvalidOperation) as e:
        logger.warning(
            "finance-api nc-actuals-monthly cc=%s fy=%s failed: %s",
            cost_center_id, fiscal_year, e,
        )
        return None
