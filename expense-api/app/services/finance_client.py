"""HTTP client — forward payment execution to finance-api (Phase 0-B1.5).

finance-api's POST /finance/v1/payments/execute is THE single payment
implementation (unified can_pay, status flip, payment_records, posting event).
This service's /pay endpoints are thin forwards.

Error mapping: 403 → PermissionError, 404 → LookupError, 409 → ValueError,
anything else → RuntimeError.
"""
import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=2.0)


async def execute_payment(
    *,
    doc_kind: str,                     # pa | pa_dir | expense_claim
    doc_id: uuid.UUID,
    bearer_token: str,
    payment_date: date | str | None = None,
    payment_method: str = "bank_transfer",
    reference: str | None = None,
    amount_paid: Decimal | None = None,
    notes: str | None = None,
    bank_account_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "doc_kind": doc_kind,
        "doc_id": str(doc_id),
        "payment_method": payment_method,
    }
    if payment_date is not None:
        payload["payment_date"] = str(payment_date)
    if reference is not None:
        payload["reference"] = reference
    if amount_paid is not None:
        payload["amount_paid"] = str(amount_paid)
    if notes is not None:
        payload["notes"] = notes
    if bank_account_id is not None:
        payload["bank_account_id"] = str(bank_account_id)

    url = f"{settings.finance_api_url}/finance/v1/payments/execute"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
    except httpx.HTTPError as e:
        logger.error("finance-api /payments/execute unreachable: %s", e)
        raise RuntimeError(f"Finance service unreachable at {settings.finance_api_url}")

    if resp.status_code == 403:
        raise PermissionError(resp.json().get("detail", "Insufficient permissions"))
    if resp.status_code == 404:
        raise LookupError(resp.json().get("detail", "Document not found"))
    if resp.status_code == 409:
        raise ValueError(resp.json().get("detail", "Invalid document status"))
    if not resp.is_success:
        logger.error("finance-api /payments/execute %d: %s", resp.status_code, resp.text[:200])
        raise RuntimeError(f"Finance service error {resp.status_code}")
    return resp.json()


async def upsert_ap_invoice(*, payload: dict, bearer_token: str) -> dict | None:
    """Upsert a normalized AP invoice header into finance ap_invoices (source='oa').

    Fail-open: finance unavailability must not block OA invoice ops (logged; a
    later re-sync / reconciliation re-posts). payload must be JSON-ready.

    When finance_api_url is unset/blank, AP sync is treated as disabled and is
    skipped entirely (no HTTP). Lets the test suite opt out of cross-service
    writes so it can never pollute a live finance-api (see tests/conftest.py)."""
    if not (settings.finance_api_url or "").strip():
        return None
    url = f"{settings.finance_api_url}/finance/v1/ap/invoices"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        logger.error("finance-api /ap/invoices upsert failed for %s/%s: %s — failing open",
                     payload.get("source"), payload.get("source_invoice_id"), e)
        return None
