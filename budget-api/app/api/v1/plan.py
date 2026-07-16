"""Budget Plan endpoints — CRUD + state transitions + grid rendering."""
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status

from app.core.authz import require_permission
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud import plan as plan_crud
from app.schemas.plan import (
    BaselineResponse, BreakdownCreate, BreakdownResponse, BreakdownUpdate,
    BulkBreakdownReplaceRequest, BulkBreakdownReplaceResponse,
    PlanActionRequest, PlanCreate, PlanGridResponse, PlanImportResult,
    PlanLineResponse, PlanLineUpdate, PlanResponse, PlanReviseRequest, PlanUpdate,
)
from app.services import approval_client
from app.services.plan_aggregator import build_plan_grid

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plan"])


@router.get("/plans", response_model=list[PlanResponse])
async def list_plans(
    db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
    cost_center_id: uuid.UUID | None = Query(default=None),
    fiscal_year: int | None = Query(default=None),
    status: str | None = Query(default=None),
    include_history: bool = Query(
        default=False,
        description="If true, also return superseded prior versions (is_current=False).",
    ),
):
    plans = await plan_crud.list_plans(
        db, cost_center_id=cost_center_id, fiscal_year=fiscal_year, status=status,
        include_history=include_history,
    )
    return [PlanResponse.model_validate(p) for p in plans]


@router.get(
    "/plans/by-cc-year/{cost_center_id}/{fiscal_year}/versions",
    response_model=list[PlanResponse],
)
async def list_versions(
    cost_center_id: uuid.UUID, fiscal_year: int,
    db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
):
    """Full version chain for one (cc, fy) — newest first."""
    versions = await plan_crud.list_plan_versions(db, cost_center_id, fiscal_year)
    return [PlanResponse.model_validate(p) for p in versions]


@router.post("/plans", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    payload: PlanCreate, db: SessionDep,
    user: dict = Depends(require_permission("budget.plan.write")),
):
    actor_id = uuid.UUID(user["sub"])
    plan = await plan_crud.create_plan(db, payload, actor_id)
    return PlanResponse.model_validate(plan)


@router.get("/plans/{plan_id}", response_model=PlanGridResponse)
async def get_plan_grid(
    plan_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
):
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    return await build_plan_grid(db, plan)


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: uuid.UUID, db: SessionDep,
    _: dict = Depends(require_permission("budget.plan.write")),
):
    """Delete a draft plan. CASCADE removes lines + breakdowns.

    Only `status='draft'` plans are deletable — submitted / approved plans must
    go through the approval state machine (reject / cancel) instead so the
    audit trail is preserved. Revisions of an approved parent (v2+) can be
    deleted too; the parent stays `is_current=True` (it never lost current).
    """
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    if plan.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only draft plans can be deleted (this plan is '{plan.status}'). "
            "Use the workflow Reject/Cancel action instead.",
        )
    await plan_crud.delete_plan(db, plan)


@router.patch("/plans/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: uuid.UUID, payload: PlanUpdate, db: SessionDep,
    user: dict = Depends(require_permission("budget.plan.write")),  # noqa: ARG001
):
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    plan = await plan_crud.update_plan(db, plan, payload)
    return PlanResponse.model_validate(plan)


@router.patch(
    "/plans/{plan_id}/lines/{account_id}/{month}",
    response_model=PlanLineResponse,
)
async def update_plan_line(
    plan_id: uuid.UUID, account_id: uuid.UUID, month: int,
    payload: PlanLineUpdate, db: SessionDep,
    user: dict = Depends(require_permission("budget.plan.write")),  # noqa: ARG001
):
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    line = await plan_crud.update_plan_line(db, plan, account_id, month, payload)
    return PlanLineResponse.model_validate(line)


# ── Breakdowns ────────────────────────────────────────────────────────────────

@router.get(
    "/plans/{plan_id}/lines/{account_id}/{month}/breakdowns",
    response_model=list[BreakdownResponse],
)
async def list_breakdowns_for_cell(
    plan_id: uuid.UUID, account_id: uuid.UUID, month: int, db: SessionDep,
    user: CurrentUserPayload,  # noqa: ARG001
):
    line = await plan_crud.get_plan_line(db, plan_id, account_id, month)
    if line is None:
        return []
    items = await plan_crud.list_breakdowns(db, line.id)
    return [BreakdownResponse.model_validate(b) for b in items]


@router.post(
    "/plans/{plan_id}/lines/{account_id}/{month}/breakdowns",
    response_model=BreakdownResponse, status_code=status.HTTP_201_CREATED,
)
async def create_breakdown(
    plan_id: uuid.UUID, account_id: uuid.UUID, month: int,
    payload: BreakdownCreate, db: SessionDep,
    user: dict = Depends(require_permission("budget.plan.write")),  # noqa: ARG001
):
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    bd = await plan_crud.create_breakdown(db, plan, account_id, month, payload)
    return BreakdownResponse.model_validate(bd)


@router.put(
    "/plans/{plan_id}/lines/{account_id}/{month}/breakdowns",
    response_model=BulkBreakdownReplaceResponse,
)
async def replace_breakdowns_for_cell(
    plan_id: uuid.UUID, account_id: uuid.UUID, month: int,
    payload: BulkBreakdownReplaceRequest, db: SessionDep,
    user: dict = Depends(require_permission("budget.plan.write")),  # noqa: ARG001
):
    """Atomic bulk-replace of all breakdowns for one (line) cell.

    Used by the Matrix editor to save the entire grid in one round-trip:
    DELETE all existing breakdowns + INSERT the provided list + recompute
    `plan_line.amount` — all in a single transaction.
    """
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    line, new_rows = await plan_crud.replace_breakdowns(
        db, plan, account_id, month, payload.breakdowns,
    )
    return BulkBreakdownReplaceResponse(
        breakdowns=[BreakdownResponse.model_validate(b) for b in new_rows],
        line_amount=line.amount,
    )


@router.get(
    "/plans/{plan_id}/lines/{account_id}/{month}/baseline",
    response_model=BaselineResponse,
)
async def get_baseline_for_cell(
    plan_id: uuid.UUID, account_id: uuid.UUID, month: int, db: SessionDep,
    user: CurrentUserPayload,  # noqa: ARG001
):
    """Historical reference data for the matrix sidebar."""
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    data = await plan_crud.get_baseline(db, plan, account_id, month)
    return BaselineResponse(
        last_year_same_month=data["last_year_same_month"],
        current_month_actual=data["current_month_actual"],
        ytd_actual=data["ytd_actual"],
        last_year_breakdowns=[
            BreakdownResponse.model_validate(b) for b in data["last_year_breakdowns"]
        ],
    )


@router.patch("/breakdowns/{breakdown_id}", response_model=BreakdownResponse)
async def update_breakdown(
    breakdown_id: uuid.UUID, payload: BreakdownUpdate, db: SessionDep,
    user: dict = Depends(require_permission("budget.plan.write")),  # noqa: ARG001
):
    bd = await plan_crud.get_breakdown(db, breakdown_id)
    if bd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Breakdown not found")
    from app.models.plan import BudgetPlanLine
    pl = await db.get(BudgetPlanLine, bd.plan_line_id)
    if pl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan line not found")
    plan = await plan_crud.get_plan(db, pl.plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    bd = await plan_crud.update_breakdown(db, plan, bd, payload)
    return BreakdownResponse.model_validate(bd)


@router.delete("/breakdowns/{breakdown_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_breakdown(
    breakdown_id: uuid.UUID, db: SessionDep,
    _: dict = Depends(require_permission("budget.plan.write")),
):
    bd = await plan_crud.get_breakdown(db, breakdown_id)
    if bd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Breakdown not found")
    from app.models.plan import BudgetPlanLine
    pl = await db.get(BudgetPlanLine, bd.plan_line_id)
    if pl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan line not found")
    plan = await plan_crud.get_plan(db, pl.plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    await plan_crud.delete_breakdown(db, plan, bd)


# ── State transitions ────────────────────────────────────────────────────────

@router.post("/plans/{plan_id}/action", response_model=PlanResponse)
async def plan_action(
    plan_id: uuid.UUID, payload: PlanActionRequest, db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    """Delegate to approval-api which owns the configurable workflow.

    approval-api updates budget_plans.status / approval_step_idx / submitted_at /
    approved_at directly via its BudgetPlan mirror model, and creates the next
    approver task. After the delegation succeeds we refetch and return the
    updated row.
    """
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    try:
        await approval_client.execute_plan_action(
            bearer_token=token, plan_id=plan_id,
            action=payload.action, comment=payload.comment,
        )
    except httpx.HTTPStatusError as e:
        # Surface approval-api's status/detail to the caller
        try:
            detail = e.response.json().get("detail") or e.response.text
        except Exception:  # noqa: BLE001
            detail = e.response.text
        raise HTTPException(status_code=e.response.status_code, detail=str(detail))
    except httpx.HTTPError as e:
        logger.error("approval-api call failed: %s", e)
        raise HTTPException(status_code=502, detail="approval-api unreachable")
    # Refresh plan from DB (approval-api wrote to it directly)
    await db.refresh(plan)
    return PlanResponse.model_validate(plan)


@router.post("/plans/{plan_id}/copy-from-prev", response_model=dict)
async def copy_from_prev(
    plan_id: uuid.UUID, db: SessionDep,
    user: dict = Depends(require_permission("budget.plan.write")),
):
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    actor_id = uuid.UUID(user["sub"])
    count = await plan_crud.copy_from_prev_year(db, plan, actor_id)
    return {"lines_copied": count}


@router.post(
    "/plans/{plan_id}/revise",
    response_model=PlanResponse, status_code=status.HTTP_201_CREATED,
)
async def revise_plan(
    plan_id: uuid.UUID, payload: PlanReviseRequest, db: SessionDep,
    user: dict = Depends(require_permission("budget.plan.write")),
):
    """Create a new draft revision of an approved + current plan.

    The new version copies all lines / breakdowns from the parent. It starts
    `is_current=False` so balance queries keep reading the parent until the
    revision is approved; the post-approval hook then flips current over.
    """
    parent = await plan_crud.get_plan(db, plan_id)
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    actor_id = uuid.UUID(user["sub"])
    revision = await plan_crud.revise_plan(db, parent, actor_id, payload.revision_notes)
    return PlanResponse.model_validate(revision)


# ── CSV Import / Export ───────────────────────────────────────────────────────

@router.get("/plans/{plan_id}/export")
async def export_plan(
    plan_id: uuid.UUID, db: SessionDep,
    user: CurrentUserPayload,  # noqa: ARG001
):
    """Download this plan as a wide CSV (one row per Account)."""
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    csv_text = await plan_crud.export_plan_csv(db, plan)
    filename = f"budget-plan-fy{plan.fiscal_year}-v{plan.version}.csv"
    return Response(
        content=csv_text, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/plans/{plan_id}/import",
    response_model=PlanImportResult,
)
async def import_plan(
    plan_id: uuid.UUID, file: UploadFile, db: SessionDep,
    user: dict = Depends(require_permission("budget.plan.write")),  # noqa: ARG001
):
    """Bulk-update plan_line amounts from CSV. Plan must be draft or returned."""
    plan = await plan_crud.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    content = await file.read()
    try:
        csv_text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "File is not UTF-8 — export from Excel as CSV UTF-8 and try again",
        )
    return await plan_crud.import_plan_csv(db, plan, csv_text)
