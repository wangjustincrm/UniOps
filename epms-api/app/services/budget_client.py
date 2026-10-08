"""HTTP client for the budget-api microservice.

Used by epms-api in place of the deleted in-process budget module
(crud/budget.py, models/budget.py, api/v1/budget.py).

Fail-open: on network / 5xx errors the balance lookup returns "infinite available"
so PR submission isn't blocked by budget-api downtime. The dashboard helpers
return empty summaries.
"""
from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal
from typing import Any

import httpx
from fastapi import HTTPException

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


# The L2 catalog is small (169 rows today), global — not user-scoped, budget-api
# returns the same list to everyone the way the frontend's cached catalog query
# assumes — and changes only when someone edits the chart of accounts. Every
# document write now checks a code against it, so hold it briefly rather than
# refetching per request. Only successful responses are cached; a failure must
# not pin "unreachable" for a minute.
_ACCOUNTS_TTL_SECONDS = 60.0
_accounts_cache: tuple[float, list[dict[str, Any]]] | None = None


def clear_accounts_cache() -> None:
    """Drop the cached catalog. Tests need this seam: a cache that survives
    between them makes the second test in a file see the first one's stubbed
    catalog, which looks like the code under test ignoring its own input."""
    global _accounts_cache
    _accounts_cache = None


async def get_accounts(bearer_token: str | None) -> list[dict[str, Any]] | None:
    """The budget account catalog, or None when budget-api can't be reached.

    None means "don't know", not "empty" — the two are opposite conclusions for
    a validator, so callers must not collapse them into a falsy check.
    """
    global _accounts_cache
    now = time.monotonic()
    if _accounts_cache is not None and now - _accounts_cache[0] < _ACCOUNTS_TTL_SECONDS:
        return _accounts_cache[1]
    url = f"{settings.BUDGET_API_URL}/api/v1/accounts"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=_auth_headers(bearer_token))
            r.raise_for_status()
            accounts = r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("budget-api /accounts unreachable: %s", e)
        return None
    if not isinstance(accounts, list):
        logger.warning("budget-api /accounts returned %s, not a list", type(accounts).__name__)
        return None
    _accounts_cache = (now, accounts)
    return accounts


async def get_account_name(bearer_token: str | None, code: str | None) -> str | None:
    """The budget account's display name for a given code, or None.

    budget-api exposes no by-code lookup, so this pulls the catalog and matches
    client-side -- the same thing the PR Detail page does with its cached
    catalog query.

    Fail-open like the rest of this module: a document must still render when
    budget-api is unreachable, just with the bare code as before.
    """
    if not code:
        return None
    accounts = await get_accounts(bearer_token)
    if accounts is None:
        return None
    for acct in accounts:
        if acct.get("code") == code:
            return acct.get("name") or None
    return None


async def account_code_is_known(bearer_token: str | None, code: str | None) -> bool | None:
    """Is `code` an actual budget account code?

    True / False, or **None when budget-api is unreachable** — the caller cannot
    tell a bad code from a missing catalog, and must decide which way to fail.
    A blank code is True: the column is nullable and "no budget account" is a
    legitimate state on every document that carries it.
    """
    if not code:
        return True
    accounts = await get_accounts(bearer_token)
    if accounts is None:
        return None
    return any(acct.get("code") == code for acct in accounts)


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


async def get_actuals_lineage(bearer_token: str | None) -> dict[str, Any] | None:
    """What the plan and actual (docs) figures are made of, from budget-api.

    The other half of the Budget Dashboard's explanation (finance-api owns the
    NC half). Carries the live operation mix, which is the difference between
    describing the design and saying what is currently true of the ledger.
    Returns None when budget-api cannot be reached.
    """
    url = f"{settings.BUDGET_API_URL}/api/v1/actuals/lineage"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=_auth_headers(bearer_token))
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        logger.warning("budget-api /actuals/lineage unreachable: %s", e)
        return None


async def ensure_known_budget_code(bearer_token: str | None, code: str | None) -> None:
    """Reject a budget_code that is not an account code. Raises 422.

    `purchase_requests.budget_code` / `purchase_orders.budget_code` /
    `purchase_agreements.budget_code` are plain text with no foreign key to the
    catalog, and for a long time nothing checked them: the PR form wrote
    "<code>-<name>" until 2026-07-17 and the PO form was a free-text box with
    "e.g. CRM003-01" as its placeholder. 6,099 rows had to be repaired on
    2026-09-18. The forms now pick from the catalog; this is the half that also
    holds for anything posting to the API directly.

    **Fail-open when budget-api is unreachable.** The pickers that produce this
    value are themselves filled from budget-api, so during an outage a user
    cannot choose a code at all and the only writes left are legitimate ones
    replaying an existing value. Refusing them would turn a budget-service blip
    into "no purchase orders can be saved", which is a worse failure than the
    one this guards against.

    Data Maintenance is deliberately not routed through here: it exists to write
    values the normal path forbids, and an admin repairing a row is the one
    caller who may need to.
    """
    known = await account_code_is_known(bearer_token, code)
    if known is None:
        logger.warning(
            "budget-api unreachable — accepting budget_code %r unchecked", code,
        )
        return
    if not known:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{code}' is not a budget account code. Pick an account from the "
                f"list — the code alone, without the account name."
            ),
        )
