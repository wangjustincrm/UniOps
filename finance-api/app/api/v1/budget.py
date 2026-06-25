"""Budget endpoints — thin proxy to budget-api (:8007).

The finance-api budget URL surface is preserved so existing callers
(PA workflow in expense-api / approval flow) don't change. Internally,
every operation forwards to budget-api which owns the data.

All writes are idempotent via budget_ledger.unique(source_service, source_doc_type, source_doc_id, operation).
"""
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import CurrentUser
from app.schemas.budget import (
    BudgetActualizeRequest, BudgetBalanceResponse,
    BudgetCommitRequest, BudgetCommitResponse, BudgetReleaseRequest,
)
from app.services import budget_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/budget", tags=["budget"])


def _get_bearer_token_from_user(_user: CurrentUser) -> str | None:
    """finance-api dependency CurrentUser parses the JWT but doesn't expose the raw token.
    For now we forward without auth — budget-api will accept the call from the
    service-to-service network. Production should use a dedicated service token.
    """
    return None


@router.get("/balance", response_model=BudgetBalanceResponse)
async def get_balance(
    budget_code: str = Query(...),
    cost_center_id: uuid.UUID = Query(...),
    fiscal_year: int = Query(...),
    user: CurrentUser = ...,
):
    token = _get_bearer_token_from_user(user)
    try:
        data = await budget_client.get_balance(
            bearer_token=token, cost_center_id=cost_center_id,
            fiscal_year=fiscal_year, account_code=budget_code,
        )
    except httpx.HTTPError as e:
        logger.error("budget-api /balance failed: %s", e)
        raise HTTPException(status_code=502, detail="budget-api unreachable")
    if data is None:
        raise HTTPException(status_code=404, detail="Budget account not found")
    return BudgetBalanceResponse(
        budget_code=data.get("account_code", budget_code),
        cost_center_id=cost_center_id,
        annual_budget=data["annual_budget"],
        committed=data["committed"],
        actual_spent=data["actual_spent"],
        available=data["available"],
    )


@router.post("/commit", response_model=BudgetCommitResponse)
async def commit_budget(
    body: BudgetCommitRequest, user: CurrentUser = ...,
):
    token = _get_bearer_token_from_user(user)
    fiscal_year, month = budget_client.current_month_year()
    account_id = await budget_client.resolve_account_id(
        bearer_token=token, account_code=body.budget_code,
    )
    if account_id is None:
        raise HTTPException(status_code=404, detail="Budget account not found")
    try:
        await budget_client.write_ledger(
            bearer_token=token, operation="commit",
            source_doc_type=body.document_type,
            source_doc_id=body.document_id,
            cost_center_id=body.cost_center_id,
            account_id=account_id,
            fiscal_year=fiscal_year, month=month,
            amount=body.amount,
            notes=f"{body.document_type.upper()} {body.document_number}",
        )
        # Return the new balance snapshot
        bal = await budget_client.get_balance(
            bearer_token=token, cost_center_id=body.cost_center_id,
            fiscal_year=fiscal_year, account_code=body.budget_code,
        )
    except httpx.HTTPError as e:
        logger.error("budget-api commit failed: %s", e)
        raise HTTPException(status_code=502, detail="budget-api unreachable")
    return BudgetCommitResponse(
        budget_code=body.budget_code,
        annual_budget=bal["annual_budget"] if bal else 0,
        committed_before=0,  # delta tracking removed — use /balance for absolute
        committed_after=bal["committed"] if bal else 0,
        available=bal["available"] if bal else 0,
    )


@router.post("/release", status_code=204)
async def release_budget(
    body: BudgetReleaseRequest, user: CurrentUser = ...,
):
    token = _get_bearer_token_from_user(user)
    fiscal_year, month = budget_client.current_month_year()
    account_id = await budget_client.resolve_account_id(
        bearer_token=token, account_code=body.budget_code,
    )
    if account_id is None:
        raise HTTPException(status_code=404, detail="Budget account not found")
    bal = await budget_client.get_balance(
        bearer_token=token, cost_center_id=body.cost_center_id,
        fiscal_year=fiscal_year, account_code=body.budget_code,
    )
    if bal is None:
        raise HTTPException(status_code=404, detail="Budget account not found")
    try:
        await budget_client.write_ledger(
            bearer_token=token, operation="release",
            source_doc_type=body.document_type,
            source_doc_id=body.document_id,
            cost_center_id=body.cost_center_id,
            account_id=account_id,
            fiscal_year=fiscal_year, month=month,
            amount=bal["committed"],
            notes=f"Release {body.document_type.upper()} {body.document_id}",
        )
    except httpx.HTTPError as e:
        logger.error("budget-api release failed: %s", e)
        raise HTTPException(status_code=502, detail="budget-api unreachable")


@router.post("/actualize", status_code=204)
async def actualize_budget(
    body: BudgetActualizeRequest, user: CurrentUser = ...,
):
    token = _get_bearer_token_from_user(user)
    fiscal_year, month = budget_client.current_month_year()
    account_id = await budget_client.resolve_account_id(
        bearer_token=token, account_code=body.budget_code,
    )
    if account_id is None:
        raise HTTPException(status_code=404, detail="Budget account not found")
    try:
        await budget_client.write_ledger(
            bearer_token=token, operation="actualize",
            source_doc_type=body.document_type,
            source_doc_id=body.document_id,
            cost_center_id=body.cost_center_id,
            account_id=account_id,
            fiscal_year=fiscal_year, month=month,
            amount=body.amount,
            notes=f"Actualize {body.document_type.upper()} {body.document_id}",
        )
    except httpx.HTTPError as e:
        logger.error("budget-api actualize failed: %s", e)
        raise HTTPException(status_code=502, detail="budget-api unreachable")
