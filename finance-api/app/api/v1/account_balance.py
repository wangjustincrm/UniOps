"""Account balance report API (科目余额表) — reads posted JV lines."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud import account_balance as crud
from app.db.base import get_db

router = APIRouter(prefix="/gl", tags=["account-balance"])


@router.get("/account-balance")
async def account_balance(_: CurrentUser, db: AsyncSession = Depends(get_db),
                          period: str = Query(...)):
    """① 科目余额表: opening/period/closing per account (posted JV, local CAD)."""
    return await crud.account_balance(db, period)


@router.get("/account-balance/{account_code}/dims")
async def dims(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Checkable aux dimensions for an account (coa_aux_items ∩ registry)."""
    return await crud.list_dims(db, account_code)


@router.get("/account-balance/{account_code}/expand")
async def expand(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                 period: str = Query(...),
                 dims: str = Query(default="cost_center")):
    """② dynamic expansion by a comma-separated dimension subset."""
    try:
        return await crud.expand_by_dims(db, account_code, period,
                                         [d.strip() for d in dims.split(",") if d.strip()])
    except crud.BadDims as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/account-balance/{account_code}/vouchers")
async def vouchers(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                   period: str = Query(...),
                   dims_values: str | None = Query(default=None)):
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
        return await crud.account_vouchers(db, account_code, period, dims_values=parsed)
    except crud.BadDims as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/budget-actual")
async def budget_actual(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        period: str = Query(...)):
    """④ Budget Actual: 4 expense accounts' actuals per cost center by category."""
    return await crud.budget_actual(db, period)


@router.get("/budget-actual-grid")
async def budget_actual_grid(_: CurrentUser, db: AsyncSession = Depends(get_db),
                             period: str = Query(...)):
    """④b Budget-vs-Actual grid: per (cost center × income-expense item)
    budget/actual/variance for the 5 categories + Payroll/Depreciation tie-out
    rows + unmapped exceptions. Budget comes from budget-api; if it is
    unreachable the grid still renders actuals (budget column 0)."""
    import logging

    from app.services import budget_client
    try:
        budget = await budget_client.fetch_plan_lines(int(period[:4]), int(period[5:7]))
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "budget-api plan-lines fetch failed; rendering actuals only")
        budget = {}
    return await crud.budget_actual_grid(db, period, budget)


@router.get("/nc-actuals-monthly")
async def nc_actuals_monthly(_: CurrentUser, db: AsyncSession = Depends(get_db),
                             fiscal_year: int = Query(...),
                             cost_center_id: uuid.UUID | None = Query(default=None)):
    """NC posted actual per (income-expense item × month) for a fiscal year,
    optionally scoped to a cost center — the EPMS Budget Dashboard's NC-actual line."""
    return await crud.nc_actuals_monthly(db, fiscal_year, cost_center_id)


@router.get("/nc-partner-monthly")
async def nc_partner_monthly(_: CurrentUser, db: AsyncSession = Depends(get_db),
                             income_expense_item_id: uuid.UUID = Query(...),
                             fiscal_year: int = Query(...),
                             cost_center_id: uuid.UUID | None = Query(default=None)):
    """Budget Dashboard drill: partner (客商/供应商/客户) × month NC actual for one
    budget account, cost center already locked by the caller."""
    return await crud.nc_partner_monthly(db, income_expense_item_id, fiscal_year, cost_center_id)


@router.get("/nc-partner-vouchers")
async def nc_partner_vouchers(_: CurrentUser, db: AsyncSession = Depends(get_db),
                              income_expense_item_id: uuid.UUID = Query(...),
                              fiscal_year: int = Query(...), month: int = Query(...),
                              cost_center_id: uuid.UUID | None = Query(default=None),
                              partner_id: str | None = Query(default=None)):
    """Vouchers behind one (budget account × cost center × partner × month).
    partner_id='none' => lines with no partner."""
    pid: object = partner_id
    if partner_id and partner_id != "none":
        pid = uuid.UUID(partner_id)
    return await crud.nc_partner_vouchers(db, income_expense_item_id, fiscal_year,
                                          month, cost_center_id, pid)


@router.get("/budget-actual/partner-export")
async def budget_actual_partner_export(
    request: Request,
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
    fiscal_year: int = Query(...),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    """Budget Dashboard export: one .xlsx with every predreal budget account
    expanded by vendor (客商). Plan (budget-api) + NC-posted actual, monthly + year.
    cost_center_id omitted => aggregated across all cost centers. Fail-open to
    plan=0 if budget-api is unreachable (mirrors /budget-actual-grid)."""
    import logging

    from fastapi.responses import Response
    from sqlalchemy import select as _select

    from app.models.mirrors import CostCenter
    from app.services import budget_client
    from app.services.predreal_export import build_partner_export_xlsx

    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else None

    try:
        accounts = await budget_client.fetch_monthly_summary(
            bearer_token=token, fiscal_year=fiscal_year, cost_center_id=cost_center_id)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "budget-api monthly-summary fetch failed; exporting with plan=0")
        accounts = []
    # Exclude payroll (CRM007) / depreciation (CRM004) — category-level only, same
    # as the dashboard's isPayrollOrDeprec filter.
    accounts = [a for a in accounts
                if not (str(a.get("account_code", "")).startswith("CRM004")
                        or str(a.get("account_code", "")).startswith("CRM007"))]

    nc = (await crud.nc_actuals_monthly(db, fiscal_year, cost_center_id))["accounts"]
    partners = await crud.nc_partner_monthly_all(
        db, fiscal_year=fiscal_year, cost_center_id=cost_center_id)

    if cost_center_id is not None:
        cc_name = (await db.execute(
            _select(CostCenter.name).where(CostCenter.id == cost_center_id))).scalar_one_or_none()
        cc_label = cc_name or str(cost_center_id)
    else:
        cc_label = "All Cost Centers"

    data = build_partner_export_xlsx(
        accounts=accounts, nc_monthly=nc, partners_by_account=partners,
        fiscal_year=fiscal_year, cost_center_label=cc_label)
    fname = f"budget-actual-FY{fiscal_year}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
