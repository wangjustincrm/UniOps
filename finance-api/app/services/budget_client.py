"""HTTP client for budget-api — used by finance-api to commit/release/actualize.

Replaces direct writes to BudgetAccount.committed / actual_spent.
All operations are idempotent on (source_doc_type, source_doc_id, operation).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
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
    *, bearer_token: str | None,
    cost_center_id: uuid.UUID, fiscal_year: int,
    account_code: str | None = None, account_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    params: dict[str, Any] = {
        "cost_center_id": str(cost_center_id),
        "fiscal_year": fiscal_year,
    }
    if account_code is not None:
        params["account_code"] = account_code
    if account_id is not None:
        params["account_id"] = str(account_id)
    url = f"{settings.budget_api_url}/api/v1/balance"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, params=params, headers=_auth_headers(bearer_token))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


async def book_expense(
    *,
    bearer_token: str | None,
    source_doc_id: uuid.UUID,
    source_doc_type: str,
    lines: list[dict[str, Any]],
    notes: str | None = None,
) -> dict[str, Any] | None:
    """POST /api/v1/book-expense — idempotent on (source_doc_type, source_doc_id).

    Moved here from expense-api with the claim-payment follow-ups (Phase a A0).
    The idempotency key MUST stay 'expense_claim' + claim.id — identical to the
    historical expense-api emissions, so previously-booked claims never double-book.
    Fail-open: errors are logged, payment is not blocked (same as before).
    """
    body = {
        "source_doc_id": str(source_doc_id),
        "source_doc_type": source_doc_type,
        "lines": [
            {
                "cost_center_id": str(line["cost_center_id"]),
                "account_id": str(line["account_id"]),
                "fiscal_year": int(line["fiscal_year"]),
                "month": int(line["month"]),
                "amount": str(Decimal(str(line["amount"]))),
            }
            for line in lines
        ],
        "notes": notes,
    }
    url = f"{settings.budget_api_url}/api/v1/book-expense"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=body, headers=_auth_headers(bearer_token))
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        logger.error("budget-api /book-expense failed: %s", e)
        return None


async def write_ledger(
    *, bearer_token: str | None,
    operation: str,  # commit / release / actualize
    source_doc_type: str,
    source_doc_id: uuid.UUID,
    cost_center_id: uuid.UUID,
    account_id: uuid.UUID,
    fiscal_year: int,
    month: int,
    amount: Decimal,
    notes: str | None = None,
) -> dict[str, Any]:
    body = {
        "source_doc_type": source_doc_type,
        "source_doc_id": str(source_doc_id),
        "cost_center_id": str(cost_center_id),
        "account_id": str(account_id),
        "fiscal_year": fiscal_year,
        "month": month,
        "amount": str(amount),
        "notes": notes,
    }
    url = f"{settings.budget_api_url}/api/v1/{operation}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(url, json=body, headers=_auth_headers(bearer_token))
        r.raise_for_status()
        return r.json()


async def resolve_account_id(
    *, bearer_token: str | None, account_code: str,
) -> uuid.UUID | None:
    """Look up an account UUID by code via budget-api GET /accounts."""
    url = f"{settings.budget_api_url}/api/v1/accounts"
    params = {"is_active": True}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, params=params, headers=_auth_headers(bearer_token))
        r.raise_for_status()
        for item in r.json():
            if item.get("code") == account_code:
                return uuid.UUID(item["id"])
    return None


def current_month_year() -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    return now.year, now.month
