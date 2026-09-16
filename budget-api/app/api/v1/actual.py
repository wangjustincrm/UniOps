"""Actual / committed reporting endpoints."""
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import text

from app.core.authz import require_permission
from app.core.budget_scope import resolve_budget_scope, scoped_cc_ids
from app.core.deps import CurrentUserPayload, SessionDep
from app.crud import balance as balance_crud
from app.crud import opening as opening_crud
from app.schemas.actual import (
    ActualsListResponse,
    ActualsScopeResponse,
    ActualsSummaryResponse,
    MonthlyActualsSummaryResponse,
    ScopeCostCenter,
)
from app.schemas.opening import OpeningImportResult, OpeningListResponse

router = APIRouter(tags=["actual"])


async def _scope_for(db, user: dict):
    return await resolve_budget_scope(
        db, uuid.UUID(user["sub"]), user.get("role"))


@router.get("/actuals/scope", response_model=ActualsScopeResponse)
async def actuals_scope(db: SessionDep, user: CurrentUserPayload):
    scope = await _scope_for(db, user)
    if scope.full_access:
        rows = (await db.execute(text(
            "SELECT id, code, name, department_id FROM cost_centers "
            "WHERE is_active IS TRUE ORDER BY code"))).all()
    elif scope.cost_center_ids:
        rows = (await db.execute(text(
            "SELECT id, code, name, department_id FROM cost_centers "
            "WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY code"),
            {"ids": [str(x) for x in scope.cost_center_ids]})).all()
    else:
        rows = []
    return ActualsScopeResponse(
        full_access=scope.full_access,
        cost_centers=[ScopeCostCenter(id=r[0], code=r[1], name=r[2], department_id=r[3])
                      for r in rows],
    )


@router.get("/actuals", response_model=ActualsListResponse)
async def list_actuals(
    db: SessionDep, user: CurrentUserPayload,
    cost_center_id: uuid.UUID | None = Query(default=None),
    fiscal_year: int | None = Query(default=None),
    account_id: uuid.UUID | None = Query(default=None),
    month: int | None = Query(default=None, ge=1, le=12),
):
    scope = await _scope_for(db, user)
    if scope.full_access:
        items = await balance_crud.list_monthly_actuals(
            db, cost_center_id=cost_center_id, fiscal_year=fiscal_year,
            account_id=account_id, month=month,
        )
    else:
        items = await balance_crud.list_monthly_actuals(
            db, cost_center_id=None, fiscal_year=fiscal_year,
            account_id=account_id, month=month,
            cc_ids=scoped_cc_ids(scope, cost_center_id),
        )
    return ActualsListResponse(items=items, total=len(items))


@router.get("/actuals/summary", response_model=ActualsSummaryResponse)
async def actuals_summary(
    db: SessionDep, user: CurrentUserPayload,
    fiscal_year: int = Query(..., ge=2020, le=2100),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    scope = await _scope_for(db, user)
    cc_ids = scoped_cc_ids(scope, cost_center_id)
    if scope.full_access:
        return await balance_crud.get_actuals_summary(
            db, cost_center_id=cost_center_id, fiscal_year=fiscal_year)
    return await balance_crud.get_actuals_summary(
        db, cost_center_id=None, fiscal_year=fiscal_year, cc_ids=cc_ids)


@router.get("/actuals/monthly-summary", response_model=MonthlyActualsSummaryResponse)
async def actuals_monthly_summary(
    db: SessionDep, user: CurrentUserPayload,
    fiscal_year: int = Query(..., ge=2020, le=2100),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    """Per-account plan vs actual broken down by month, for the Budget Dashboard."""
    scope = await _scope_for(db, user)
    cc_ids = scoped_cc_ids(scope, cost_center_id)
    if scope.full_access:
        return await balance_crud.get_monthly_actuals_summary(
            db, cost_center_id=cost_center_id, fiscal_year=fiscal_year)
    return await balance_crud.get_monthly_actuals_summary(
        db, cost_center_id=None, fiscal_year=fiscal_year, cc_ids=cc_ids)


@router.get("/actuals/lineage")
async def actuals_lineage(db: SessionDep, user: CurrentUserPayload):  # noqa: ARG001
    """What the plan and actual (docs) figures on the dashboard are made of.

    Describes how a figure is arrived at rather than what any figure is: no
    cost centre, no year, no amounts — so it needs no scope. Anyone who can
    reach the dashboard can ask why it says what it says."""
    from app.services import lineage
    return await lineage.describe(db)


# ── 期初 (opening balance) import ─────────────────────────────────────────────

@router.get("/actuals/opening", response_model=OpeningListResponse)
async def list_opening(
    db: SessionDep, user: CurrentUserPayload,
    fiscal_year: int = Query(..., ge=2020, le=2100),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    """List imported opening balances for a (cost_center, fiscal_year) scope."""
    scope = await _scope_for(db, user)
    if scope.full_access:
        return await opening_crud.list_opening_balances(
            db, cost_center_id=cost_center_id, fiscal_year=fiscal_year,
        )
    return await opening_crud.list_opening_balances(
        db, cost_center_id=None, fiscal_year=fiscal_year,
        cc_ids=scoped_cc_ids(scope, cost_center_id),
    )


@router.post("/actuals/opening-import", response_model=OpeningImportResult)
async def import_opening(
    db: SessionDep,
    file: UploadFile,
    cost_center_id: uuid.UUID = Form(...),
    fiscal_year: int = Form(..., ge=2020, le=2100),
    user: dict = Depends(require_permission("budget.opening.write")),  # noqa: ARG001
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
