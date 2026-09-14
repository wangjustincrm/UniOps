"""Account balance report API (科目余额表) — reads posted JV lines, or
posted + not-yet-tallied ones when `include_unposted` is set (NC's
包含未记账凭证 toggle)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.budget_scope import resolve_budget_scope, scoped_cc_ids
from app.core.deps import CurrentUser
from app.crud import account_balance as crud
from app.db.base import get_db

router = APIRouter(prefix="/gl", tags=["account-balance"])


async def _cc_scope(db: AsyncSession, user: dict, requested: uuid.UUID | None) -> dict:
    """Resolve the caller's department budget-scope into CRUD kwargs for the four
    NC-actuals functions below. Full-access callers (budget_scope.FULL_ACCESS_*)
    keep the exact old behaviour: `cost_center_id` passed through unchanged, no
    `cc_ids`. Non-full-access callers get `cost_center_id=None` (so the CRUD's
    cc_ids branch takes over) + `cc_ids` clamped to their department by
    `scoped_cc_ids` (in-scope request -> that one CC; out-of-scope/omitted ->
    the whole department; no department -> [] fail-closed). Never raises/403s —
    an out-of-scope or unresolvable caller simply sees nothing."""
    scope = await resolve_budget_scope(db, uuid.UUID(user["sub"]), user.get("role"))
    if scope.full_access:
        return {"cost_center_id": requested}
    return {"cost_center_id": None, "cc_ids": scoped_cc_ids(scope, requested)}


@router.get("/account-balance")
async def account_balance(_: CurrentUser, db: AsyncSession = Depends(get_db),
                          period: str = Query(...),
                          include_unposted: bool = Query(default=False)):
    """① 科目余额表: opening/period/closing per account (posted JV, local CAD)."""
    return await crud.account_balance(db, period, include_unposted=include_unposted)


@router.get("/account-balance/{account_code}/dims")
async def dims(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Checkable aux dimensions for an account (coa_aux_items ∩ registry)."""
    return await crud.list_dims(db, account_code)


@router.get("/account-balance/{account_code}/expand")
async def expand(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                 period: str = Query(...),
                 dims: str = Query(default="cost_center"),
                 include_unposted: bool = Query(default=False)):
    """② dynamic expansion by a comma-separated dimension subset."""
    try:
        return await crud.expand_by_dims(db, account_code, period,
                                         [d.strip() for d in dims.split(",") if d.strip()],
                                         include_unposted=include_unposted)
    except crud.BadDims as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/account-balance/{account_code}/vouchers")
async def vouchers(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                   period: str = Query(...),
                   dims_values: str | None = Query(default=None),
                   include_unposted: bool = Query(default=False)):
    """③ drill-down; dims_values = 'dim:uuid,dim:none' combo filter."""
    parsed: dict = {}
    if dims_values:
        for pair in dims_values.split(","):
            if not pair.strip():
                continue
            if ":" not in pair:
                raise HTTPException(status_code=422, detail=f"bad dims_values pair: {pair!r}")
            d, v = pair.split(":", 1)
            try:
                parsed[d.strip()] = None if v.strip() == "none" else uuid.UUID(v.strip())
            except ValueError:
                raise HTTPException(status_code=422, detail=f"bad uuid in dims_values: {v!r}")
    try:
        return await crud.account_vouchers(db, account_code, period, dims_values=parsed,
                                           include_unposted=include_unposted)
    except crud.BadDims as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/budget-actual")
async def budget_actual(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        period: str = Query(...)):
    """④ Budget Actual: 4 expense accounts' actuals per cost center by category."""
    return await crud.budget_actual(db, period)


@router.get("/budget-actual-grid")
async def budget_actual_grid(request: Request, _: CurrentUser,
                             db: AsyncSession = Depends(get_db),
                             period: str = Query(...)):
    """④b Budget-vs-Actual grid: per (cost center × income-expense item)
    budget/actual/variance for the 5 categories + Payroll/Depreciation tie-out
    rows + unmapped exceptions. Budget comes from budget-api; if it is
    unreachable the grid still renders actuals (budget column 0)."""
    import logging

    from app.services import budget_client
    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else None
    try:
        budget = await budget_client.fetch_plan_lines(
            int(period[:4]), int(period[5:7]), bearer_token=token)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "budget-api plan-lines fetch failed; rendering actuals only")
        budget = {}
    return await crud.budget_actual_grid(db, period, budget)


@router.get("/nc-actuals-monthly")
async def nc_actuals_monthly(user: CurrentUser, db: AsyncSession = Depends(get_db),
                             fiscal_year: int = Query(...),
                             cost_center_id: uuid.UUID | None = Query(default=None)):
    """NC posted actual per (income-expense item × month) for a fiscal year,
    optionally scoped to a cost center — the EPMS Budget Dashboard's NC-actual
    line. Non-full-access callers are further clamped to their department's
    cost centers (see `_cc_scope`; never 403, fail-closed to empty)."""
    kw = await _cc_scope(db, user, cost_center_id)
    return await crud.nc_actuals_monthly(db, fiscal_year, **kw)


@router.get("/nc-partner-monthly")
async def nc_partner_monthly(user: CurrentUser, db: AsyncSession = Depends(get_db),
                             income_expense_item_id: uuid.UUID = Query(...),
                             fiscal_year: int = Query(...),
                             cost_center_id: uuid.UUID | None = Query(default=None)):
    """Budget Dashboard drill: partner (客商/供应商/客户) × month NC actual for one
    budget account, cost center already locked by the caller (clamped to the
    caller's department scope if not full-access)."""
    kw = await _cc_scope(db, user, cost_center_id)
    result = await crud.nc_partner_monthly(db, income_expense_item_id, fiscal_year, **kw)
    if cost_center_id is not None:
        # `_cc_scope` may null out cost_center_id for the filter (scoped callers
        # filter by cc_ids instead) — the response still echoes back what the
        # caller asked for, independent of how it was actually filtered.
        result["cost_center_id"] = str(cost_center_id)
    return result


@router.get("/nc-partner-vouchers")
async def nc_partner_vouchers(user: CurrentUser, db: AsyncSession = Depends(get_db),
                              income_expense_item_id: uuid.UUID = Query(...),
                              fiscal_year: int = Query(...), month: int = Query(...),
                              cost_center_id: uuid.UUID | None = Query(default=None),
                              partner_id: str | None = Query(default=None)):
    """Vouchers behind one (budget account × cost center × partner × month).
    partner_id='none' => lines with no partner."""
    pid: object = partner_id
    if partner_id and partner_id != "none":
        pid = uuid.UUID(partner_id)
    kw = await _cc_scope(db, user, cost_center_id)
    return await crud.nc_partner_vouchers(db, income_expense_item_id, fiscal_year,
                                          month, partner_id=pid, **kw)


@router.get("/budget-actual/partner-export")
async def budget_actual_partner_export(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    fiscal_year: int = Query(...),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    """Budget Dashboard export: one .xlsx with a row per predreal budget account
    and two columns per month — Plan (budget-api) and NC-posted actual — plus a
    Year pair. cost_center_id omitted => aggregated across all cost centers (or,
    for a non-full-access caller, their department — see `_cc_scope`). Fail-open
    to plan=0 if budget-api is unreachable (mirrors /budget-actual-grid)."""
    import logging

    from fastapi.responses import Response
    from sqlalchemy import select as _select

    from app.models.mirrors import CostCenter
    from app.services import budget_client
    from app.services.predreal_export import build_budget_actual_xlsx

    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else None

    kw = await _cc_scope(db, user, cost_center_id)
    # Plan side: full-access callers keep requesting the exact cost_center_id
    # (kw["cost_center_id"] == requested); scoped callers pass None and rely on
    # budget-api scoping this SAME caller's bearer token to their own
    # department (budget-api Task 3) rather than trusting cc_ids computed here.
    try:
        accounts = await budget_client.fetch_monthly_summary(
            bearer_token=token, fiscal_year=fiscal_year,
            cost_center_id=kw["cost_center_id"])
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "budget-api monthly-summary fetch failed; exporting with plan=0")
        accounts = []
    # Exclude payroll (CRM007) / depreciation (CRM004) — category-level only, same
    # as the dashboard's isPayrollOrDeprec filter.
    accounts = [a for a in accounts
                if not (str(a.get("account_code", "")).startswith("CRM004")
                        or str(a.get("account_code", "")).startswith("CRM007"))]

    nc = (await crud.nc_actuals_monthly(db, fiscal_year, **kw))["accounts"]

    if cost_center_id is not None:
        cc_name = (await db.execute(
            _select(CostCenter.name).where(CostCenter.id == cost_center_id))).scalar_one_or_none()
        cc_label = cc_name or str(cost_center_id)
    else:
        cc_label = "All Cost Centers"

    data = build_budget_actual_xlsx(
        accounts=accounts, nc_monthly=nc,
        fiscal_year=fiscal_year, cost_center_label=cc_label)
    fname = f"budget-actual-FY{fiscal_year}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
