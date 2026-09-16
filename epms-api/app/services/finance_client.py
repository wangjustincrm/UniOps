"""HTTP client — forward payment execution to finance-api (Phase 0-B1.5).

finance-api's POST /finance/v1/payments/execute is THE single payment
implementation (unified can_pay, status flip, payment_records, posting event,
PA-PO invoice marking). EPMS's PA action=process forwards there instead of
the approval engine.

Error mapping: 403 → PermissionError, 404 → LookupError, 409 → ValueError,
anything else → RuntimeError.
"""
import logging
import uuid
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=2.0)


async def post_invoice(*, invoice_id: uuid.UUID, bearer_token: str) -> dict[str, Any] | None:
    """A2: notify finance to emit the AP accrual for a matched invoice.

    Fail-open: accrual emission must not block the 3-way match (idempotent —
    a later re-match or the A5 reconciliation re-posts it); errors are logged.
    """
    url = f"{settings.FINANCE_API_URL}/finance/v1/ap/post-invoice"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url, json={"invoice_id": str(invoice_id)},
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        logger.error("finance-api /ap/post-invoice failed for %s: %s — failing open", invoice_id, e)
        return None


async def execute_payment(
    *,
    doc_kind: str,                     # pa | pa_dir
    doc_id: uuid.UUID,
    bearer_token: str,
    notes: str | None = None,
    bank_account_id: uuid.UUID | None = None,   # funding bank/card (records + GL)
    credit_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """credit_ids is THREE-VALUED and must be compared with `is None` — it is
    forwarded verbatim to finance-api's PaymentExecuteRequest.credit_ids:
      None  -> omitted from the body, so finance-api applies its automatic
               FIFO default (this is what every caller did before Phase B)
      []    -> sent as [], meaning "apply no vendor credit this run"
      [ids] -> sent as-is, meaning "apply only these"
    `if credit_ids:` would collapse [] into None and silently net a payment the
    operator asked to pay in full — hence the explicit `is not None`.
    """
    payload: dict[str, Any] = {
        "doc_kind": doc_kind,
        "doc_id": str(doc_id),
        "payment_method": "bank_transfer",
    }
    if notes is not None:
        payload["notes"] = notes
    if bank_account_id is not None:
        payload["bank_account_id"] = str(bank_account_id)
    if credit_ids is not None:
        payload["credit_ids"] = [str(c) for c in credit_ids]

    url = f"{settings.FINANCE_API_URL}/finance/v1/payments/execute"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
    except httpx.HTTPError as e:
        logger.error("finance-api /payments/execute unreachable: %s", e)
        raise RuntimeError(f"Finance service unreachable at {settings.FINANCE_API_URL}")

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


async def mark_ap_settled(*, invoice_ids: list[uuid.UUID], bearer_token: str) -> dict | None:
    """Zero-cash settlement: tell finance to flip these invoices' ap_invoices to
    paid (a prepayment already covered them — no new cash).

    Fail-open: AP writeback must not block the EPMS reconciliation (idempotent —
    a later resync re-flips); errors are logged.
    """
    if not invoice_ids:
        return None
    url = f"{settings.FINANCE_API_URL}/finance/v1/ap/mark-settled"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url, json={"invoice_ids": [str(i) for i in invoice_ids]},
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        logger.error("finance-api /ap/mark-settled failed for %s: %s — failing open", invoice_ids, e)
        return None


async def upsert_ap_invoice(*, payload: dict, bearer_token: str) -> dict | None:
    """Upsert a normalized AP invoice header+tax into finance ap_invoices.

    Fail-open: finance unavailability must not block EPMS invoice ops (errors
    logged; a later re-sync / reconciliation re-posts). payload must be JSON-ready
    (uuids/dates/decimals already stringified by the caller).
    """
    url = f"{settings.FINANCE_API_URL}/finance/v1/ap/invoices"
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

async def resync_pa_writeback(*, pa_id: uuid.UUID, bearer_token: str) -> dict | None:
    """Ask finance-api to replay a PAID PA's invoice + AP writeback against the
    PA's current invoice_ids.

    Called from Data Maintenance AFTER the edit has been committed — finance-api
    reads the shared DB on its own connection and cannot see an uncommitted
    change (the same constraint that makes mark_ap_settled's placement matter).

    Fail-open like the rest of this client: a repair that lands in EPMS but
    cannot reach finance must not roll back the repair. The caller surfaces the
    failure as a warning so it is visible rather than silent.
    """
    url = f"{settings.FINANCE_API_URL}/finance/v1/ap/resync-pa-writeback"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json={"pa_id": str(pa_id)},
                                  headers={"Authorization": f"Bearer {bearer_token}"})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error("finance-api /ap/resync-pa-writeback failed for %s: %s", pa_id, e)
        raise


async def budget_actual_lineage(*, bearer_token: str | None) -> dict | None:
    """How the NC-posted figures on the Budget Dashboard are arrived at.

    Rules, not rows: the five expense categories, what is excluded, and the
    order the cost-centre map is consulted in, read off the objects finance-api
    actually runs. The assistant explains the report with this rather than with
    a description of it kept over here, which would drift the first time finance
    changed the map's shape.

    Returns None when finance-api cannot be reached — the caller says that part
    is unavailable rather than filling the gap in from memory.
    """
    url = f"{settings.FINANCE_API_URL}/finance/v1/gl/budget-actual/lineage"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                url, headers={"Authorization": f"Bearer {bearer_token}"} if bearer_token else {})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        logger.warning("finance-api /gl/budget-actual/lineage unreachable: %s", e)
        return None
