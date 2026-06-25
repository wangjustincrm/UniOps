"""HTTP client for budget-api — used by expense-api to write actual spend
to the central budget ledger (replaces direct writes to BudgetAccount.actual_spent).
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


async def book_expense(
    *,
    bearer_token: str | None,
    source_doc_id: uuid.UUID,
    source_doc_type: str,
    lines: list[dict[str, Any]],
    notes: str | None = None,
) -> dict[str, Any] | None:
    """POST /api/v1/book-expense — idempotent on (source_doc_type, source_doc_id).

    Each line must contain: cost_center_id (str/UUID), account_id (str/UUID),
    fiscal_year (int), month (int), amount (Decimal/str).
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
