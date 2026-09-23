"""Account balance report API (科目余额表) — reads posted JV lines, or
posted + not-yet-tallied ones when `include_unposted` is set (NC's
包含未记账凭证 toggle)."""
import uuid
from decimal import Decimal

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


@router.get("/budget-actual/lineage")
async def budget_actual_lineage(_: CurrentUser):
    """The rule behind the NC-posted figures, in words, for the assistant.

    No period, no rows, no scope: it describes how a number is arrived at, not
    what any number is. It is served from here because the rule is here — the
    categories, the exclusions and the resolver order are read off the objects
    the sync and the grid use, so this cannot describe a rule that stopped
    being true."""
    from app.services import budget_actual_lineage as lineage
    return lineage.describe()


@router.get("/budget-actual/rollup")
async def budget_actual_rollup(_: CurrentUser, db: AsyncSession = Depends(get_db),
                               fiscal_year: int = Query(...),
                               month_from: int = Query(default=1, ge=1, le=12),
                               month_to: int | None = Query(default=None, ge=1, le=12)):
    """Company / expense centre / department / cost centre roll-up for the
    finance Budget-vs-Actual report.

    `month_from`..`month_to` covers every window finance asked for with one pair:
    a single month (9..9), a quarter (7..9), an arbitrary range, or year-to-date
    (1..9, the default). `month_to` defaults to the current month when the
    requested year is the current one, and to December otherwise — asking for
    "2025" should not return three quarters of it because today is September."""
    from datetime import date

    from app.crud import budget_rollup
    if month_to is None:
        today = date.today()
        month_to = today.month if fiscal_year == today.year else 12
    if month_from > month_to:
        raise HTTPException(status_code=422, detail="month_from is after month_to")
    return await budget_rollup.rollup(db, fiscal_year=fiscal_year,
                                      month_from=month_from, month_to=month_to)


@router.get("/budget-actual/rollup/export")
async def budget_actual_rollup_export(
        _: CurrentUser, db: AsyncSession = Depends(get_db),
        fiscal_year: int = Query(...),
        month_from: int = Query(default=1, ge=1, le=12),
        month_to: int | None = Query(default=None, ge=1, le=12),
        group_by: str = Query(default="centre")):
    """The roll-up as an .xlsx, grouped the way the page is grouped.

    The tree is written out fully expanded — a collapsed row on screen is a
    convenience, a missing row in a spreadsheet is missing data."""
    from datetime import date

    from fastapi.responses import Response

    from app.crud import budget_rollup
    from app.services.budget_rollup_export import build_rollup_xlsx

    if group_by not in ("centre", "department"):
        raise HTTPException(status_code=422, detail=f"unknown group_by {group_by!r}")
    if month_to is None:
        today = date.today()
        month_to = today.month if fiscal_year == today.year else 12
    if month_from > month_to:
        raise HTTPException(status_code=422, detail="month_from is after month_to")

    data = await budget_rollup.rollup(db, fiscal_year=fiscal_year,
                                      month_from=month_from, month_to=month_to)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    label = (months[month_from - 1] if month_from == month_to
             else f"{months[month_from - 1]}-{months[month_to - 1]}")
    xlsx = build_rollup_xlsx(rollup=data, group_by=group_by, window_label=label)
    name = f"budget-actual-{group_by}-FY{fiscal_year}-{label}.xlsx"
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/budget-actual/breakdown")
async def budget_actual_breakdown(_: CurrentUser, db: AsyncSession = Depends(get_db),
                                  fiscal_year: int = Query(...),
                                  month_from: int = Query(default=1, ge=1, le=12),
                                  month_to: int | None = Query(default=None, ge=1, le=12),
                                  scope_kind: str = Query(default="company"),
                                  scope_key: str = Query(default="")):
    """What a roll-up figure is made of: budget account × cost centre, over the
    same window and the same rules as the roll-up above it.

    `scope_kind` mirrors what the reader clicked — company / centre (expense
    centre prefix) / department (code) / cost_centre (code)."""
    from datetime import date

    from app.crud import budget_rollup
    if scope_kind not in ("company", "centre", "department", "cost_centre"):
        raise HTTPException(status_code=422, detail=f"unknown scope_kind {scope_kind!r}")
    if scope_kind != "company" and not scope_key:
        raise HTTPException(status_code=422,
                            detail=f"scope_key is required for scope_kind={scope_kind}")
    if month_to is None:
        today = date.today()
        month_to = today.month if fiscal_year == today.year else 12
    if month_from > month_to:
        raise HTTPException(status_code=422, detail="month_from is after month_to")
    return await budget_rollup.breakdown(
        db, fiscal_year=fiscal_year, month_from=month_from, month_to=month_to,
        scope_kind=scope_kind, scope_key=scope_key)


@router.get("/budget-actual/unallocated-lines")
async def budget_actual_unallocated_lines(
        _: CurrentUser, db: AsyncSession = Depends(get_db),
        fiscal_year: int = Query(...),
        month_from: int = Query(default=1, ge=1, le=12),
        month_to: int | None = Query(default=None, ge=1, le=12),
        bucket: str = Query(...),
        limit: int = Query(default=200, ge=1, le=1000),
        offset: int = Query(default=0, ge=0)):
    """The voucher lines behind one bucket of the roll-up's unallocated row.

    `bucket` is an excluded income-expense prefix (CRM004 / CRM007 / CRM09912)
    or the literal `__unplaced__` for lines that reach no cost centre for any
    other reason — the bucket a reader most needs to open, since every line in
    it is something nobody decided where to put."""
    from datetime import date

    from app.crud import budget_rollup
    allowed = set(budget_rollup.EXCLUDED_IO_PREFIXES) | {"__unplaced__"}
    if bucket not in allowed:
        raise HTTPException(status_code=422, detail=f"unknown bucket {bucket!r}")
    if month_to is None:
        today = date.today()
        month_to = today.month if fiscal_year == today.year else 12
    if month_from > month_to:
        raise HTTPException(status_code=422, detail="month_from is after month_to")
    return await budget_rollup.unallocated_lines(
        db, fiscal_year=fiscal_year, month_from=month_from, month_to=month_to,
        bucket=bucket, limit=limit, offset=offset)


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


@router.get("/nc-actuals-by-cost-center")
async def nc_actuals_by_cost_center(user: CurrentUser, db: AsyncSession = Depends(get_db),
                                    fiscal_year: int = Query(...),
                                    through_month: int = Query(default=12, ge=1, le=12),
                                    cost_center_id: uuid.UUID | None = Query(default=None)):
    """NC posted actual per cost centre, year to date through a month.

    The dashboard's own figure summed by cost centre instead of by budget
    account, so a year's plan and what has been spent against it can be put
    side by side. Scoped exactly like the other NC reads (`_cc_scope`), and it
    reports what the window drops as well as what it totals."""
    kw = await _cc_scope(db, user, cost_center_id)
    cc_ids = kw.get("cc_ids")
    if kw.get("cost_center_id") is not None:
        cc_ids = [kw["cost_center_id"]]
    return await crud.nc_actuals_by_cost_center(
        db, fiscal_year, through_month=through_month, cc_ids=cc_ids)


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
    """Vouchers behind one (budget account × cost center × party × month).

    `partner_id` is the `key` a partner row reports, and is passed through as an
    opaque string: a vendor uuid, the raw NC code for a vendor with no UniOps
    record, or a bucket name ('multi' — voucher names several parties, 'none' —
    no party anywhere on the voucher). It used to be parsed as a uuid, which
    422'd on every non-uuid key the breakdown can now produce."""
    kw = await _cc_scope(db, user, cost_center_id)
    pid: object = partner_id
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


@router.get("/nc-actual-for-budget-check")
async def nc_actual_for_budget_check(
        _: CurrentUser, db: AsyncSession = Depends(get_db),
        fiscal_year: int = Query(...),
        cost_center_id: uuid.UUID = Query(...),
        account_id: uuid.UUID = Query(...)):
    """NC posted actual for ONE (cost center × budget account × year), as the
    year total — budget-api's `/balance` calls this so a PR's over-budget test
    is decided on the same number the Budget Dashboard shows.

    ★ Deliberately NOT scoped by `_cc_scope`, unlike every other NC read here.
    Those are reports: showing a reader less than the whole company is right,
    and returning nothing when scope can't be resolved is a safe default. This
    one is a *criterion*. Clamping it would not hide a number from anybody — it
    would silently answer 0 for a cost center outside the caller's department
    (a dept_admin raising a PR for another department, a user with no
    department at all), `available` would come back as the full annual budget,
    and every such PR would sail through as within budget. A fail-closed report
    becomes a fail-open control. The criterion has to see the facts.

    The exposure this adds is one aggregate for a (cc, account) the caller
    already names — the same call chain's `/balance` hands back that pair's
    annual_budget and committed with no scoping at all, and budget-api is
    published on its own public hostname exactly as this service is.
    Voucher-level reads (`/nc-partner-vouchers` and friends) keep their clamp.

    ★★ READ THIS BEFORE GATING THIS ROUTER (`fix/finance-authz-and-ap-status`)

    That branch puts a `FinanceRead` dependency on 43 reads here, and leaves
    four `/gl/nc-*` routes open on a stated ground: "these already clamp rows
    to the caller's department via _cc_scope". **That ground does not hold for
    this endpoint** — it is deliberately unclamped (above) — so it cannot be
    waved through on the same sentence, and neither of the two obvious moves
    is right on its own:

      * Gate it with `FinanceRead` and an ordinary requester raising a PR gets
        403 here. budget-api treats that as "unknown" and falls back to the
        ledger, which is the very opening-balance bug this endpoint exists to
        fix — restored silently, for everyone without a finance role.
      * Leave it off the list and it is ungated AND unclamped: strictly wider
        than the four routes that list does name.

    The resolution this wants is the service token that branch already records
    as owed ("These still need a service token — tracked, not fixed here", on
    POST /ap/invoices): budget-api should call this with a service identity
    instead of forwarding the end user's token, and then it can be closed to
    end users entirely. Until that exists, a caller-agnostic answer and an open
    door are the same decision, and the over-budget gate is what depends on it.
    A new permission key is NOT a cheap substitute: it needs an identity
    migration, and production runs only migrate-prod.sh, so the key would not
    exist there and every caller would 403.

    Also load-bearing, and easy to lose: the CRUD below counts only
    `JournalVoucher.status == POSTED`. Voided / draft / errored NC vouchers are
    imported and marked now rather than dropped at sync, and they are held at
    `draft` — so they stay out of this figure because of that filter and for no
    other reason. Any future rewrite that reaches the lines without it starts
    charging cancelled vouchers against people's budgets.
    """
    # Same CRUD the dashboard's NC line runs, so the two can never drift: one
    # definition of "NC posted actual", asked here for a single account.
    data = await crud.nc_actuals_monthly(
        db, fiscal_year, cost_center_id=cost_center_id, account_id=account_id)
    months = (data.get("accounts") or {}).get(str(account_id)) or {}
    total = sum((Decimal(str(v)) for v in months.values()), Decimal("0"))
    return {
        "fiscal_year": fiscal_year,
        "cost_center_id": str(cost_center_id),
        "account_id": str(account_id),
        "actual": str(total),
    }
