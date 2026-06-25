"""Actual / committed reporting endpoints."""
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status

from app.core.deps import CurrentUserPayload, SessionDep, require_roles
from app.crud import balance as balance_crud
from app.crud import opening as opening_crud
from app.schemas.actual import (
    ActualsListResponse,
    ActualsSummaryResponse,
    MonthlyActualsSummaryResponse,
)
from app.schemas.opening import OpeningImportResult, OpeningListResponse

router = APIRouter(tags=["actual"])

_OPENING_WRITE_ROLES = ("finance_manager", "finance_bp")


@router.get("/actuals", response_model=ActualsListResponse)
async def list_actuals(
    db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
    cost_center_id: uuid.UUID | None = Query(default=None),
    fiscal_year: int | None = Query(default=None),
    account_id: uuid.UUID | None = Query(default=None),
    month: int | None = Query(default=None, ge=1, le=12),
):
    items = await balance_crud.list_monthly_actuals(
        db, cost_center_id=cost_center_id, fiscal_year=fiscal_year,
        account_id=account_id, month=month,
    )
    return ActualsListResponse(items=items, total=len(items))


@router.get("/actuals/summary", response_model=ActualsSummaryResponse)
async def actuals_summary(
    db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
    fiscal_year: int = Query(..., ge=2020, le=2100),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    return await balance_crud.get_actuals_summary(
        db, cost_center_id=cost_center_id, fiscal_year=fiscal_year,
    )


@router.get("/actuals/monthly-summary", response_model=MonthlyActualsSummaryResponse)
async def actuals_monthly_summary(
    db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
    fiscal_year: int = Query(..., ge=2020, le=2100),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    """Per-account plan vs actual broken down by month, for the Budget Dashboard."""
    return await balance_crud.get_monthly_actuals_summary(
        db, cost_center_id=cost_center_id, fiscal_year=fiscal_year,
    )


# ── 期初 (opening balance) import ─────────────────────────────────────────────

@router.get("/actuals/opening", response_model=OpeningListResponse)
async def list_opening(
    db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
    fiscal_year: int = Query(..., ge=2020, le=2100),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    """List imported opening balances for a (cost_center, fiscal_year) scope."""
    return await opening_crud.list_opening_balances(
        db, cost_center_id=cost_center_id, fiscal_year=fiscal_year,
    )


@router.post("/actuals/opening-import", response_model=OpeningImportResult)
async def import_opening(
    db: SessionDep,
    file: UploadFile,
    cost_center_id: uuid.UUID = Form(...),
    fiscal_year: int = Form(..., ge=2020, le=2100),
    user: dict = Depends(require_roles(*_OPENING_WRITE_ROLES)),  # noqa: ARG001
):
    """Import opening balances from a wide CSV (Account Code + Jan..Dec) scoped to
    one cost center + fiscal year. Re-importing updates existing values."""
    content = await file.read()
    try:
        csv_text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "File is not UTF-8 — export from Excel as CSV UTF-8 and try again",
        )
    return await opening_crud.import_opening_csv(
        db, cost_center_id=cost_center_id, fiscal_year=fiscal_year, csv_text=csv_text,
    )
